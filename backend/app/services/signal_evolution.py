"""Isolated stock research preset and bounded job orchestration."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from pathlib import Path

import polars as pl

from app.backtest.signal_evolution import KEYS, evaluate
from app.enriched_generation import (
    EnrichedGenerationUnavailableError,
    get_enriched_generation_snapshot,
)
from app.factors.registry import (
    factor_calculation_order,
    factor_warmup_bars,
    get_factor,
    isolated_registry,
)
from app.services.pipeline_jobs import (
    JobCancelledError,
    JobStore,
    is_cancelled,
    request_cancel,
    run_with_capacity,
)
from app.strategy import custom_signals
from app.strategy.premarket_signal_definitions import SIGNALS
from app.strategy.scoring import materialize_scoring_columns
from app.tickflow.repository import KlineRepository

logger = logging.getLogger(__name__)
PRESET = 'mabull-amihud-v1'
START, END = date(2026, 4, 13), date(2026, 11, 3)
SYMBOLS = ('000001.SZ', '300750.SZ', '600000.SH')
SIGNAL_ID = 't_mabull'
FACTOR_ID = 'amihud_20d'
TIMEOUT_S = 30 * 60
_BATCH_DAYS = 12


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      default=lambda item: sorted(item) if isinstance(item, frozenset) else item.isoformat())


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _definition(data_dir: Path) -> dict:
    definitions = [s for s in SIGNALS if s['id'] == SIGNAL_ID]
    path = data_dir / 'user_data' / 'custom_signals' / f'{SIGNAL_ID}.json'
    try:
        local = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        raise ValueError('无法读取同 ID 的本地信号定义') from exc
    else:
        definitions.append(local)
    if not definitions or any(d != definitions[0] for d in definitions):
        raise ValueError(f'信号定义冲突：{SIGNAL_ID}')
    return definitions[0]


def _calendar(data_dir: Path) -> list[date]:
    try:
        raw = json.loads((data_dir / 'trading_calendar.json').read_text(encoding='utf-8'))
        if not isinstance(raw, list):
            raise ValueError('invalid calendar')
        days = [date.fromisoformat(day) for day in raw]
        if days != sorted(set(days)) or START not in days or END not in days:
            raise ValueError('incomplete calendar')
        return days
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError('本地交易日历无效或不覆盖研究日期') from exc


def _clean_source(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.select(KEYS).null_count().row(0) != (0, 0) or frame.select(KEYS).is_duplicated().any():
        raise ValueError('日线数据存在空键或重复证券日期键')
    return frame.with_columns([
        pl.when(pl.col(col).is_finite() & (pl.col(col) > 0)).then(pl.col(col)).otherwise(None).alias(col)
        for col in ('close', 'amount')
    ]).sort(KEYS)


def materialize_features(source: pl.DataFrame, days: list[date], symbols: list[str], definition: dict,
                         factor_id: str = FACTOR_ID) -> pl.DataFrame:
    """A dense calendar prevents rolling factors from silently bridging an absent bar."""
    if not set(source['date'].to_list()) <= set(days) or not set(source['symbol'].to_list()) <= set(symbols):
        raise ValueError('日线数据不在请求的交易日或证券范围内')
    grid = pl.DataFrame({'symbol': symbols}).join(pl.DataFrame({'date': days}), how='cross')
    frame = grid.join(source, on=KEYS, how='left', validate='1:1').sort(KEYS)
    column = custom_signals.resolve_column(definition)
    exprs = custom_signals.build_expressions([definition])
    if column not in exprs:
        raise ValueError('无法编译研究信号')
    frame = custom_signals.materialize_factor_columns(frame, exprs, trading_dates=days)
    frame = materialize_scoring_columns(frame, [factor_id])
    if factor_id not in frame.columns:
        raise ValueError('数值因子不可物化')
    frame = custom_signals.inject(frame, exprs)
    return frame.select(*KEYS, 'close', pl.col(factor_id).alias('factor'), pl.col(column).alias('signal'))


def run_preset(data_dir: Path, *, checkpoint: Callable[[], None], progress: Callable[[int, str], None]) -> dict:
    checkpoint()
    calendar = _calendar(data_dir)
    generation = get_enriched_generation_snapshot(data_dir, initialize=False, recover=False)
    definition = _definition(data_dir)
    with isolated_registry():
        custom_signals.validate(definition)
        factor = get_factor(FACTOR_ID)
        if factor is None:
            raise ValueError('研究因子未注册')
        roots = {FACTOR_ID, *custom_signals.build_expressions([definition])[custom_signals.resolve_column(definition)].meta.root_names()}
        deps = {key: asdict(spec) for key in sorted(roots | set(factor_calculation_order(roots)))
                if (spec := get_factor(key)) is not None}
        warmup = max(factor_warmup_bars(roots), definition['data_contract']['inputs']['close']) - 1
        left, right = calendar.index(START), calendar.index(END)
        if left < warmup:
            raise ValueError('研究窗前缺少足够的交易日历预热日期')
        days, research_days = calendar[left - warmup:right + 1], calendar[left:right + 1]
        chunks = []
        for i in range(0, len(days), _BATCH_DAYS):
            checkpoint()
            chunk = days[i:i + _BATCH_DAYS]
            frame = KlineRepository.read_enriched_daily_partitions(data_dir, list(SYMBOLS), chunk)
            if not set(frame['date'].to_list()) <= set(chunk):
                raise ValueError('行情日期与分区批次不一致')
            chunks.append(frame)
            progress(5 + (i + len(chunk)) * 45 // len(days), '读取研究日线')
        current = get_enriched_generation_snapshot(data_dir, initialize=False, recover=False)
        if current != generation or calendar != _calendar(data_dir):
            raise ValueError('研究读取期间数据代次或日历发生变化')
        source = _clean_source(pl.concat(chunks))
        observed = source.filter(pl.col('date') >= START)
        if observed.is_empty() or observed['close'].drop_nulls().is_empty():
            raise ValueError('研究窗口内没有有效日线收盘价')
        progress(55, '从隔离定义物化因子和信号')
        features = materialize_features(source, days, list(SYMBOLS), definition).filter(pl.col('date') >= START)
        progress(70, '生成训练候选、冻结并评价留出段')
        result = evaluate(features, research_days, symbols=SYMBOLS, signal_condition_count=len(definition['conditions']),
                          checkpoint=checkpoint)
    progress(95, '保存隔离研究快照')
    input_rows = json.loads(_json(source.to_dicts()))
    result['snapshot'] = {
        'version': 1, 'preset': PRESET, 'asset_type': 'stock', 'source': 'enriched_adjusted_daily',
        'generation': generation.generation, 'range_generation': generation.through(END),
        'symbols': list(SYMBOLS), 'dates': [d.isoformat() for d in research_days],
        'warmup_dates': [d.isoformat() for d in days[:warmup]], 'calendar_hash': _digest(days),
        'observed_rows': len(observed), 'input_hash': _digest(input_rows),
        'signal': definition['id'], 'factor': factor.id, 'factor_version': factor.version,
        'factor_unit': 'decimal_return_per_100million_cny', 'definition_hash': _digest(definition),
        'dependencies_hash': _digest(deps), 'candidate_budget': 17, 'target': 'adjusted_close_to_close_3d',
        'unknown_policy': 'all_inputs_required', 'threshold_quantiles': [0.2, 0.4, 0.6, 0.8],
    }
    result['_frozen_inputs'] = {'rows': input_rows, 'definition': definition, 'dependency_specs': json.loads(_json(deps))}
    return result


class SignalEvolutionJobs:
    def __init__(self, data_dir: Path, *, output_dir: Path | None = None) -> None:
        self.data_dir = data_dir
        self.store = JobStore(store_dir=(output_dir or data_dir) / 'signal_evolution' / 'v1' / 'jobs')
        self._gate = threading.Lock()

    def create(self) -> tuple[str, bool, float]:
        deadline = time.monotonic() + TIMEOUT_S
        with self._gate:
            job_id, created = self.store.create(timeout_s=TIMEOUT_S)
        return job_id, created, deadline

    def cancel(self, job_id: str) -> bool:
        with self._gate:
            job = self.store.get(job_id)
            return bool(job and job['status'] in ('pending', 'running') and request_cancel(job_id))

    def run(self, job_id: str, deadline: float) -> None:
        def checkpoint():
            if is_cancelled(job_id):
                raise JobCancelledError(job_id)
            if time.monotonic() >= deadline:
                raise TimeoutError('研究任务达到 30 分钟总时限')

        def progress(percent: int, message: str):
            checkpoint()
            self.store.progress(job_id, 'research', percent, message)

        try:
            result = run_with_capacity(job_id, lambda: run_preset(self.data_dir, checkpoint=checkpoint, progress=progress),
                                       store=self.store, deadline=deadline)
            with self._gate:
                checkpoint()
                self.store.succeed(job_id, result, require_persisted=True)
        except JobCancelledError:
            self.store.fail(job_id, '研究任务已取消')
        except EnrichedGenerationUnavailableError:
            self.store.fail(job_id, '日线缓存代次不可用或正在发布')
        except (ValueError, TimeoutError) as exc:
            self.store.fail(job_id, str(exc))
        except Exception:
            logger.exception('signal evolution failed: %s', job_id)
            self.store.fail(job_id, '研究计算或快照保存失败')
