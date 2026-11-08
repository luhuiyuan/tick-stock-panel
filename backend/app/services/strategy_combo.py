"""策略组合探索：在「信号 × 策略命中矩阵」上自动枚举组合、按前瞻收益排名、样本外防过拟合。

口径（改口径要同步改前端与测试）：
- 信号池(pool)与策略复盘 `runs.parquet` 同 schema：一行 = 一个 (signal_date, symbol)
  信号，`strategy_ids` 为该信号命中的策略列表，`strategy_count` 为命中数，
  `ret_1/3/5` 为扣双边成本(默认 0.15%)后的净收益，`status_*` 为 win/lose/pending。
- **组合绝不重跑策略**：阶段 B 的全部统计都是对已算好的信号池做集合/位运算，
  只对「信号子集」重新聚合胜率，绝不再调用 engine.run。这根治了「每个组合回测一遍」
  带来的组合数膨胀与慢策略卡全局锁问题。
- 组合分两类：
  * combo（具体策略组合）：枚举 size=2..max_combo_size 的策略子集，信号需同时命中子集全部策略；
  * resonance（泛化共振规则）：按 strategy_count 的 >=k / ==k 分层，不限定具体策略。
- 命中判定：给参与组合的策略编号，每行一个 bitmask；`(row_mask & combo_mask)==combo_mask`
  即交集命中。resonance 用池自带的 strategy_count（不受参与策略截断影响）。
- 统计档位 horizon 默认 1（隔日卖口径）；净收益 >0 为赢，<=0 为输；pending 不计入分母。
- 综合分(默认排名目标) = 胜率 × min(盈亏比, PLR_CAP) × (0.5 + 0.5×稳定性)，
  稳定性 = min(1, 最低分段胜率 / 整体胜率)。可选排名目标：win_rate / avg_ret。
- IS/OOS：按池内唯一交易日升序，前 is_ratio(默认 70%) 为样本内(IS)，其余为样本外(OOS)。
  IS 按综合分取 Top-N(默认 10)，终榜按 OOS 综合分重排，报告 overfit_gap 与退化警示。
- 阶段 A(扫描，异步/独立 JobStore/独立锁/可取消/带超时)：按 batch_size(默认 3) 分批复用
  `StrategyReviewService.scan`，每批结果缓存到 `data/strategy_combo/cache/<sig>.parquet`，
  合并去重生成 pool。单日预检排除慢策略(>precheck_timeout)与运行报错策略，覆盖状态如实标注。
- 与策略复盘完全解耦：本模块不 import 复盘的 `_BUILD_LOCK` / `_STATE`，用自己的模块级状态，
  扫描线程为 daemon 且不写任何共享全局表，即使个别策略超时也不影响复盘页与其它功能。
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import statistics
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import polars as pl

from app.services.pipeline_jobs import JobStore, is_cancelled, request_cancel
from app.services.strategy_review import (
    DEFAULT_COST_PCT,
    HORIZONS,
    StrategyReviewService,
    _SCHEMA,
    _is_date,
    _market_dates,
    _stats,
    empty_frame,
)

logger = logging.getLogger(__name__)

HORIZON_DEFAULT = 1
IS_RATIO_DEFAULT = 0.70
MIN_SAMPLES_DEFAULT = 50
TOP_N_DEFAULT = 10
SEGMENTS = 4
PLR_CAP = 3.0
MAX_COMBO_SIZE_DEFAULT = 3
MAX_POOL_STRATEGIES = 12          # 参与「具体组合」枚举的策略上限(按命中数取前列)
# 阶段 A 每批策略数。用 1(单策略分批, 仍满足方案「≤3」): 每个 strategy_ids 只装
# 一个策略时 build_strategy_context 的历史窗口最小, 冷启动/宽窗口策略不会把整批
# 拖过超时; 慢策略/报错策略能精确标注到单个策略的覆盖状态, 不会连累同批其它策略。
BATCH_SIZE = 1
PRECHECK_TIMEOUT_S = 30           # 单策略单日预检超时(秒)
BATCH_TIMEOUT_S = 300             # 单批区间扫描超时(秒)
SCAN_JOB_TIMEOUT_S = 3600
RANK_BY = ("composite", "win_rate", "avg_ret")
RESONANCE_RULES = ((">=", 2), (">=", 3), (">=", 4), ("==", 1))

# ── 模块级独立状态（不共享复盘页的锁/缓存）──
_STATE: dict = {"pool": None, "meta": {}, "source": "", "mtime": 0.0}
_POOL_LOCK = threading.Lock()
_SCAN_LOCK = threading.Lock()      # 阶段 A 单飞：同一时刻只跑一个扫描任务
_JOB_STORES: dict[str, JobStore] = {}
_JOB_LOCK = threading.Lock()


def _combo_job_store(data_dir: Path) -> JobStore:
    key = str(data_dir)
    with _JOB_LOCK:
        store = _JOB_STORES.get(key)
        if store is None:
            store = JobStore(max_jobs=20, store_dir=Path(data_dir) / "strategy_combo_jobs")
            _JOB_STORES[key] = store
        return store


def _pool_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "strategy_combo"


# ════════════════════════════════════════════════════════════════
# 阶段 B：纯函数（可单测，不依赖行情数据 / 服务状态）
# ════════════════════════════════════════════════════════════════

def pool_strategies(pool: pl.DataFrame, max_strategies: int = MAX_POOL_STRATEGIES) -> list[str]:
    """按命中信号数降序取参与组合枚举的策略。"""
    counts: dict[str, int] = {}
    for value in pool.get_column("strategy_ids").to_list():
        for sid in str(value or "").split(","):
            sid = sid.strip()
            if sid:
                counts[sid] = counts.get(sid, 0) + 1
    # 按命中数降序取前列，再统一按 id 排序返回：enumerate_combos / build_arrays /
    # pair_heatmap / combo_signal_ids 都基于同一份有序列表做位编号，口径必须一致。
    ordered = sorted(counts, key=lambda s: (-counts[s], s))[:max_strategies]
    return sorted(ordered)


def enumerate_combos(strategy_ids: list[str], max_size: int = MAX_COMBO_SIZE_DEFAULT) -> list[dict]:
    """枚举具体策略组合(size 2..max_size) + 泛化共振规则。"""
    out: list[dict] = []
    ids = sorted(strategy_ids)
    bit_of = {sid: 1 << i for i, sid in enumerate(ids)}
    for size in range(2, min(max_size, len(ids)) + 1):
        for sub in itertools.combinations(ids, size):
            cmask = 0
            for sid in sub:
                cmask |= bit_of[sid]
            out.append({
                "key": "combo:" + "+".join(sub),
                "label": " ∩ ".join(sub),
                "kind": "combo",
                "strategy_ids": list(sub),
                "size": size,
                "cmask": cmask,
            })
    for op, k in RESONANCE_RULES:
        out.append({
            "key": f"res:{op}{k}",
            "label": f"命中策略数 {op} {k}",
            "kind": "resonance",
            "strategy_ids": [],
            "size": k,
            "op": op,
            "k": k,
        })
    return out


def build_arrays(pool: pl.DataFrame, horizon: int, strategy_ids: list[str],
                 is_ratio: float, segments: int = SEGMENTS):
    """把信号池摊成 numpy 数组，一次预计算供所有组合复用。"""
    n = pool.height
    ret = np.array(
        [np.nan if v is None else float(v) for v in pool.get_column(f"ret_{horizon}").to_list()],
        dtype=float,
    )
    decided = ~np.isnan(ret)
    win = decided & (ret > 0.0)
    ords = np.array([_is_date(d).toordinal() for d in pool.get_column("signal_date").to_list()], dtype=np.int64)
    counts = np.array(pool.get_column("strategy_count").to_list(), dtype=np.int16)

    bit_of = {sid: 1 << i for i, sid in enumerate(strategy_ids)}
    masks = np.zeros(n, dtype=np.int64)
    col = pool.get_column("strategy_ids").to_list()
    for r in range(n):
        m = 0
        for sid in str(col[r] or "").split(","):
            bit = bit_of.get(sid.strip())
            if bit:
                m |= bit
        masks[r] = m

    uniq = np.unique(ords) if n else np.array([], dtype=np.int64)
    # split_idx = 前 is_ratio 比例的交易日数；分界日 = OOS 的第一个交易日。
    # IS = ords < 分界日，OOS = ords >= 分界日 —— 精确 70/30（用 <= 会把第
    # split_idx+1 天也算进 IS，变成 80/20）。clamp 保证两段都至少有一天。
    split_idx = min(int(len(uniq) * is_ratio), max(len(uniq) - 1, 0)) if len(uniq) else 0
    split_date_ord = int(uniq[split_idx]) if len(uniq) else 0
    is_mask = ords < split_date_ord
    oos_mask = ords >= split_date_ord
    if len(uniq) and segments > 1:
        bounds = np.array([uniq[min(len(uniq) - 1, int(len(uniq) * k / segments))]
                           for k in range(1, segments)], dtype=np.int64)
        seg = np.searchsorted(bounds, ords, side="right").astype(np.int16)
    else:
        seg = np.zeros(n, dtype=np.int16)
    return {
        "n": n, "ret": ret, "decided": decided, "win": win, "ords": ords,
        "counts": counts, "masks": masks, "is_mask": is_mask, "oos_mask": oos_mask,
        "seg": seg, "segments": segments, "split_date_ord": split_date_ord,
        "uniq": uniq,
    }


def _hit(arr: dict, combo: dict) -> np.ndarray:
    if combo["kind"] == "combo":
        cmask = combo["cmask"]
        return (arr["masks"] & cmask) == cmask
    k, op = combo["k"], combo["op"]
    return arr["counts"] >= k if op == ">=" else arr["counts"] == k


def _stat(arr: dict, sel: np.ndarray, rank_by: str) -> dict | None:
    """对一个布尔选择集算胜负/盈亏比/分段稳定性/综合分。sel 需已含 decided。"""
    ret, win, seg = arr["ret"], arr["win"], arr["seg"]
    idx = np.nonzero(sel)[0]
    m = int(idx.size)
    if m == 0:
        return None
    rs = ret[idx]
    w = int((rs > 0).sum())
    wr = w / m
    avg = float(rs.mean())
    wins = rs[rs > 0]
    loses = rs[rs <= 0]
    if wins.size and loses.size and loses.mean() != 0:
        plr = float(wins.mean() / abs(loses.mean()))
    else:
        plr = PLR_CAP if wins.size else 0.0
    seg_wrs: list[float | None] = []
    for s in range(arr["segments"]):
        seg_sel = sel & (seg == s)
        cnt = int(seg_sel.sum())
        seg_wrs.append(float((win & seg_sel).sum() / cnt) if cnt else None)
    present = [v for v in seg_wrs if v is not None]
    stability = min(1.0, min(present) / wr) if len(present) >= 2 and wr > 0 else 1.0
    # 综合分基于「对外展示的 round 后字段」计算，保证前端/测试能用展示值
    # 精确反推公式（否则用全精度中间量算出的 score 与展示字段会对不上小数）。
    wr_r, avg_r = round(wr, 6), round(avg, 6)
    plr_r, stability_r = round(plr, 4), round(stability, 4)
    composite = wr_r * min(plr_r, PLR_CAP) * (0.5 + 0.5 * stability_r)
    if rank_by == "win_rate":
        score = wr_r
    elif rank_by == "avg_ret":
        score = avg_r
    else:
        score = composite
    return {
        "n": m, "win": w, "lose": m - w, "win_rate": wr_r,
        "avg_ret": avg_r, "median_ret": round(float(np.median(rs)), 6),
        "profit_loss_ratio": plr_r,
        "seg_wrs": [round(v, 4) if v is not None else None for v in seg_wrs],
        "min_seg_wr": round(min(present), 4) if present else None,
        "stability": stability_r,
        "score": round(score, 6),
        "composite": round(composite, 6),
    }


def evaluate(pool: pl.DataFrame, *, horizon: int = HORIZON_DEFAULT,
             rank_by: str = "composite", min_samples: int = MIN_SAMPLES_DEFAULT,
             top_n: int = TOP_N_DEFAULT, is_ratio: float = IS_RATIO_DEFAULT,
             max_combo_size: int = MAX_COMBO_SIZE_DEFAULT,
             max_strategies: int = MAX_POOL_STRATEGIES) -> dict:
    """在信号池上枚举全部组合并排名。返回 overall / rows(终榜) / meta。"""
    if rank_by not in RANK_BY:
        rank_by = "composite"
    horizon = horizon if horizon in HORIZONS else HORIZON_DEFAULT
    strategies = pool_strategies(pool, max_strategies)
    combos = enumerate_combos(strategies, max_combo_size)
    arr = build_arrays(pool, horizon, strategies, is_ratio)

    # 全量三档统计（与复盘页 /summary 交叉核对）
    full_stats = _stats(pool)
    overall = {str(h): full_stats[str(h)] for h in HORIZONS}

    rows: list[dict] = []
    for combo in combos:
        sel_all = arr["decided"] & _hit(arr, combo)
        st_all = _stat(arr, sel_all, rank_by)
        st_is = _stat(arr, sel_all & arr["is_mask"], rank_by)
        st_oos = _stat(arr, sel_all & arr["oos_mask"], rank_by)
        # 最小样本门槛按「全区间已决样本」判，避免 40 日区间切 70/30 后 IS 段
        # 普遍不足 50 把大量有效组合挡在门外；IS/OOS 仅用于两段式验证与退化警示。
        insufficient = st_all is None or st_all["n"] < min_samples
        is_score = st_is["score"] if st_is else None
        oos_score = st_oos["score"] if st_oos else None
        gap = round(is_score - oos_score, 6) if is_score is not None and oos_score is not None else None
        degraded = bool(is_score and is_score > 0 and oos_score is not None and oos_score < 0.5 * is_score)
        rows.append({
            "key": combo["key"], "label": combo["label"], "kind": combo["kind"],
            "strategy_ids": combo["strategy_ids"], "size": combo["size"],
            "all": st_all, "is": st_is, "oos": st_oos,
            "is_score": is_score, "oos_score": oos_score, "overfit_gap": gap,
            "degraded": degraded, "insufficient": insufficient,
        })

    # IS 排名选 Top-N（合格：全区间样本达标且 IS/OOS 两段均有数据），终榜按 OOS 分重排
    qualified = [
        r for r in rows
        if not r["insufficient"] and r["is"] and r["oos"] and r["is_score"] is not None
    ]
    qualified.sort(key=lambda r: r["is_score"], reverse=True)
    shortlist = qualified[:top_n]
    shortlist.sort(key=lambda r: (r["oos_score"] is not None, r["oos_score"] or -1e9), reverse=True)
    for rank, r in enumerate(shortlist, 1):
        r["final_rank"] = rank

    uniq = arr["uniq"]
    split_label = date.fromordinal(arr["split_date_ord"]).isoformat() if len(uniq) else None
    meta = {
        "horizon": horizon, "rank_by": rank_by, "min_samples": min_samples,
        "top_n": top_n, "is_ratio": is_ratio, "max_combo_size": max_combo_size,
        "strategies": strategies, "segments": SEGMENTS, "plr_cap": PLR_CAP,
        "combo_count": len(combos), "qualified_count": len(qualified),
        "shortlist_count": len(shortlist), "total_signals": arr["n"],
        "decided_signals": int(arr["decided"].sum()),
        "split_date": split_label,
        "is_days": int((arr["is_mask"] & arr["decided"]).sum()),
        "oos_days": int((arr["oos_mask"] & arr["decided"]).sum()),
        "degraded_count": sum(1 for r in shortlist if r["degraded"]),
        "note": "综合分=胜率×min(盈亏比,3)×(0.5+0.5×稳定性)；IS 选 Top-N、终榜按 OOS 重排。"
                "结果使用信号日之后的行情计算，属历史复盘、含未来数据，不构成实盘信号。"
                "枚举大量组合存在多重比较风险，OOS 与最小样本门槛用于缓解过拟合。",
    }
    # 附全部组合（供前端「全部组合」视图，按全区间综合分排序）
    all_rows = sorted(
        (r for r in rows if r["all"] is not None),
        key=lambda r: r["all"]["score"], reverse=True,
    )
    return {"meta": meta, "overall": overall, "rows": shortlist, "all_rows": all_rows}


def pair_heatmap(pool: pl.DataFrame, *, horizon: int = HORIZON_DEFAULT,
                 max_strategies: int = MAX_POOL_STRATEGIES) -> dict:
    """两两策略交集胜率矩阵（含对角=单策略）。"""
    horizon = horizon if horizon in HORIZONS else HORIZON_DEFAULT
    strategies = pool_strategies(pool, max_strategies)
    arr = build_arrays(pool, horizon, strategies, IS_RATIO_DEFAULT)
    bit_of = {sid: 1 << i for i, sid in enumerate(strategies)}
    cells: list[list[dict | None]] = []
    for a in strategies:
        row_cells: list[dict | None] = []
        for b in strategies:
            cmask = bit_of[a] | bit_of[b]
            sel = arr["decided"] & ((arr["masks"] & cmask) == cmask)
            cnt = int(sel.sum())
            if cnt == 0:
                row_cells.append(None)
            else:
                wr = float((arr["win"] & sel).sum() / cnt)
                row_cells.append({"win_rate": round(wr, 4), "n": cnt,
                                  "avg_ret": round(float(arr["ret"][sel].mean()), 4)})
        cells.append(row_cells)
    return {"strategies": strategies, "horizon": horizon, "cells": cells}


def combo_signal_ids(pool: pl.DataFrame, key: str, max_strategies: int = MAX_POOL_STRATEGIES) -> np.ndarray:
    """解析组合 key，返回命中信号的行号（布尔数组）。供下钻明细使用。"""
    strategies = pool_strategies(pool, max_strategies)
    arr = build_arrays(pool, HORIZON_DEFAULT, strategies, IS_RATIO_DEFAULT)
    bit_of = {sid: 1 << i for i, sid in enumerate(strategies)}
    if key.startswith("combo:"):
        sub = key[len("combo:"):].split("+")
        cmask = 0
        for sid in sub:
            cmask |= bit_of.get(sid, 0)
        if not cmask:
            return np.zeros(pool.height, dtype=bool)
        return (arr["masks"] & cmask) == cmask
    if key.startswith("res:"):
        expr = key[len("res:"):]
        if expr.startswith(">="):
            return arr["counts"] >= int(expr[2:])
        if expr.startswith("=="):
            return arr["counts"] == int(expr[2:])
    return np.zeros(pool.height, dtype=bool)


# ════════════════════════════════════════════════════════════════
# 服务：信号池加载、阶段 A 扫描编排、查询门面
# ════════════════════════════════════════════════════════════════

class StrategyComboService:
    def __init__(self, repo, engine=None, data_dir: Path | None = None) -> None:
        self.repo = repo
        self.engine = engine
        if data_dir is None:
            data_dir = Path(getattr(getattr(repo, "store", None), "data_dir", "."))
        self.data_dir = Path(data_dir)

    # ── 信号池 ──────────────────────────────────────────────────
    def _review_service(self) -> StrategyReviewService:
        return StrategyReviewService(self.repo, engine=self.engine, data_dir=self.data_dir)

    def _own_pool_path(self) -> Path:
        return _pool_dir(self.data_dir) / "pool.parquet"

    def _own_meta_path(self) -> Path:
        return _pool_dir(self.data_dir) / "meta.json"

    def _review_pool_path(self) -> Path:
        return self.data_dir / "strategy_review" / "runs.parquet"

    def _read_pool_file(self, path: Path) -> pl.DataFrame:
        table = pl.read_parquet(path)
        missing = [c for c in _SCHEMA if c not in table.columns]
        if missing:
            raise ValueError(f"pool missing columns: {missing}")
        return table.select(list(_SCHEMA)).cast(_SCHEMA, strict=False)

    def pool_state(self) -> tuple[pl.DataFrame, dict, str, bool]:
        """返回 (pool, meta, source, computed)。优先本功能扫描产物，回退复盘页结果。

        回退让页面开箱即用（复用已有的多策略信号池），并可立即做回归验证。
        """
        candidates = [
            (self._own_pool_path(), self._own_meta_path(), "strategy_combo"),
            (self._review_pool_path(), self.data_dir / "strategy_review" / "meta.json", "strategy_review_fallback"),
        ]
        chosen: tuple[Path, Path, str] | None = None
        for pool_p, meta_p, source in candidates:
            if pool_p.exists():
                chosen = (pool_p, meta_p, source)
                break
        if chosen is None:
            return empty_frame(), {}, "", False
        pool_p, meta_p, source = chosen
        mtime = pool_p.stat().st_mtime
        with _POOL_LOCK:
            cached = _STATE["pool"]
            if (isinstance(cached, pl.DataFrame) and _STATE["source"] == source
                    and _STATE["mtime"] == mtime):
                return cached, dict(_STATE["meta"]), source, True
        try:
            table = self._read_pool_file(pool_p)
        except Exception:
            logger.warning("strategy_combo: 信号池读取失败 %s", pool_p, exc_info=True)
            return empty_frame(), {}, source, False
        meta: dict = {}
        if meta_p.exists():
            try:
                meta = json.loads(meta_p.read_text("utf-8"))
            except Exception:
                meta = {}
        meta.setdefault("source", source)
        meta.setdefault("signals", int(table.height))
        if table.height:
            dates = [_is_date(d) for d in table.get_column("signal_date").to_list()]
            meta.setdefault("start", min(dates).isoformat())
            meta.setdefault("end", max(dates).isoformat())
        with _POOL_LOCK:
            _STATE.update({"pool": table, "meta": meta, "source": source, "mtime": mtime})
        return table, dict(meta), source, True

    def coverage(self) -> dict:
        pool, meta, source, computed = self.pool_state()
        cov = meta.get("coverage") or {}
        # 若信号池来自回退源但存在一次「0 信号扫描」的排除报告，把它作为覆盖状态
        # 展示出来（用户能看到哪些策略被预检/超时排除），信号池本身仍用回退源。
        if not cov and source != "strategy_combo":
            cov_path = _pool_dir(self.data_dir) / "coverage.json"
            if cov_path.exists():
                try:
                    cov = json.loads(cov_path.read_text("utf-8")).get("coverage") or {}
                except Exception:
                    cov = {}
        if not cov and computed and pool.height:
            present = pool_strategies(pool, 999)
            cov = {sid: {"status": "ok", "rows": 0} for sid in present}
            for sid in present:
                cov[sid]["rows"] = int(
                    pool.filter(pl.col("strategy_ids").str.contains(sid, literal=True)).height
                )
        return {
            "computed": computed, "source": source,
            "meta": {k: v for k, v in meta.items() if k != "coverage"},
            "coverage": cov,
        }

    # ── 阶段 A 扫描（异步、独立锁、可取消、带超时）─────────────
    def _cache_dir(self) -> Path:
        d = _pool_dir(self.data_dir) / "cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _cache_sig(self, ids: list[str], start: date, end: date, cost: float) -> str:
        raw = f"{','.join(sorted(ids))}|{start}|{end}|{cost}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def _scan_batch(self, ids: list[str], start: date, end: date, cost: float,
                    job_id: str) -> pl.DataFrame:
        """在独立 daemon 线程跑一批 scan，超时则跳过（线程不写共享状态）。"""
        box: dict = {"table": None, "error": None, "done": False}

        def progress(pct: int, msg: str) -> None:
            if is_cancelled(job_id):
                raise RuntimeError("任务已取消")

        def run() -> None:
            try:
                table, _ = self._review_service().scan(
                    strategy_ids=ids, start=start, end=end, cost_pct=cost, progress=progress,
                )
                box["table"] = table
            except Exception as exc:  # noqa: BLE001
                box["error"] = str(exc)
            finally:
                box["done"] = True

        t = threading.Thread(target=run, daemon=True, name=f"combo-scan-{job_id}")
        t.start()
        t.join(BATCH_TIMEOUT_S)
        if t.is_alive():
            raise TimeoutError(f"批次扫描超时 >{BATCH_TIMEOUT_S}s: {','.join(ids)}")
        if box["error"]:
            raise RuntimeError(box["error"])
        return box["table"] if box["table"] is not None else empty_frame()

    def _precheck(self, sid: str, day: date, cost: float, job_id: str) -> dict:
        """单策略单日预检：慢/报错策略提前排除，避免整批被拖垮。"""
        import time
        box: dict = {"error": None, "done": False}

        def progress(pct: int, msg: str) -> None:
            if is_cancelled(job_id):
                raise RuntimeError("任务已取消")

        def run() -> None:
            try:
                self._review_service().scan(
                    strategy_ids=[sid], start=day, end=day, cost_pct=cost, progress=progress,
                )
            except Exception as exc:  # noqa: BLE001
                box["error"] = str(exc)
            finally:
                box["done"] = True

        t0 = time.monotonic()
        t = threading.Thread(target=run, daemon=True, name=f"combo-precheck-{sid}")
        t.start()
        t.join(PRECHECK_TIMEOUT_S)
        elapsed = round(time.monotonic() - t0, 1)
        if t.is_alive():
            return {"status": "too_slow", "reason": f"单日预检 >{PRECHECK_TIMEOUT_S}s", "elapsed": elapsed}
        if box["error"]:
            return {"status": "error", "reason": box["error"][:160], "elapsed": elapsed}
        return {"status": "ok", "elapsed": elapsed}

    def run_scan(self, *, strategy_ids: list[str], start: date, end: date,
                 cost_pct: float = DEFAULT_COST_PCT, force: bool = False,
                 progress: Callable[[int, str], None] | None = None,
                 job_id: str = "") -> dict:
        """同步执行阶段 A（由 worker 线程调用），生成 pool.parquet + meta.json。"""
        if not strategy_ids:
            raise ValueError("至少选择一个策略")
        if start > end:
            raise ValueError("开始日期不能晚于结束日期")
        if self.engine is None:
            raise ValueError("策略引擎未初始化")

        calendar = _market_dates(self.data_dir)
        if not calendar:
            raise ValueError("本地没有 enriched 日线数据")
        probe_day = max(d for d in calendar if d <= end) if any(d <= end for d in calendar) else calendar[-1]

        def report(pct: int, msg: str) -> None:
            if progress:
                progress(pct, msg)
            if job_id and is_cancelled(job_id):
                raise RuntimeError("任务已取消")

        # ① 逐策略单日预检 → 覆盖状态
        coverage: dict[str, dict] = {}
        usable: list[str] = []
        total = len(strategy_ids)
        for i, sid in enumerate(strategy_ids, 1):
            report(int(i / total * 15), f"预检 {i}/{total}: {sid}")
            cov = self._precheck(sid, probe_day, cost_pct, job_id)
            if cov["status"] != "ok":
                coverage[sid] = cov
            else:
                usable.append(sid)
        report(18, f"预检完成：可用 {len(usable)}/{total}，排除 {total - len(usable)}")

        # ② 分批扫描（缓存可增量复用）
        batches = [usable[i:i + BATCH_SIZE] for i in range(0, len(usable), BATCH_SIZE)]
        frames: list[pl.DataFrame] = []
        nbat = max(len(batches), 1)
        for b, ids in enumerate(batches, 1):
            report(18 + int(b / nbat * 60), f"扫描批次 {b}/{nbat}: {','.join(ids)}")
            sig = self._cache_sig(ids, start, end, cost_pct)
            cpath = self._cache_dir() / f"{sig}.parquet"
            if cpath.exists() and not force:
                try:
                    frames.append(self._read_pool_file(cpath))
                    for sid in ids:
                        coverage.setdefault(sid, {"status": "cached"})
                    continue
                except Exception:
                    cpath.unlink(missing_ok=True)
            try:
                table = self._scan_batch(ids, start, end, cost_pct, job_id)
                tmp = cpath.with_suffix(".parquet.tmp")
                table.write_parquet(tmp)
                os.replace(tmp, cpath)
                frames.append(table)
                for sid in ids:
                    coverage.setdefault(sid, {"status": "ok"})
            except Exception as exc:  # noqa: BLE001
                logger.warning("strategy_combo: 批次 %s 失败: %s", ids, exc)
                for sid in ids:
                    coverage.setdefault(sid, {"status": "error", "reason": str(exc)[:160]})

        # ③ 合并：按 (signal_date, symbol) 并集去重，strategy_ids 累加
        report(82, f"合并 {len(frames)} 批结果")
        merged = self._merge_frames(frames)
        folder = _pool_dir(self.data_dir)
        folder.mkdir(parents=True, exist_ok=True)

        # 覆盖状态补命中行数
        for sid, cov in coverage.items():
            cov["rows"] = int(merged.filter(pl.col("strategy_ids").str.contains(sid, literal=True)).height)
            if cov["status"] == "ok" and cov.get("rows", 0) == 0:
                cov["status"] = "no_signal"

        # 空结果保护：全部批次超时/报错 → 0 信号时【不写 pool.parquet/meta.json】。
        # 否则一个失败的扫描会用空池覆盖策略复盘那份可用的多策略信号池，让页面
        # 默认态变空（历史踩过的坑）。覆盖状态单独落到 coverage.json 供 /meta 读取，
        # 用户能看到「哪些策略被排除、为什么」，但信号池仍复用回退源。
        if merged.height == 0:
            report(100, "扫描未产出有效信号，保留原有信号池（未覆盖）")
            cov_path = folder / "coverage.json"
            cov_tmp = folder / "coverage.json.tmp"
            cov_tmp.write_text(json.dumps({
                "source": "strategy_combo_scan_empty", "start": start.isoformat(),
                "end": end.isoformat(), "strategy_ids": list(strategy_ids),
                "coverage": coverage,
                "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "note": "本次扫描 0 信号（策略被预检排除或批次超时/报错），信号池未覆盖，"
                        "组合评估继续使用上一次可用结果或策略复盘回退池。",
            }, ensure_ascii=False), encoding="utf-8")
            os.replace(cov_tmp, cov_path)
            return {"signals": 0, "usable": len(usable), "excluded": total - len(usable),
                    "kept_previous_pool": True}

        tmp = folder / "pool.parquet.tmp"
        merged.write_parquet(tmp)
        os.replace(tmp, folder / "pool.parquet")
        meta = {
            "source": "strategy_combo", "start": start.isoformat(), "end": end.isoformat(),
            "strategy_ids": list(strategy_ids), "cost_pct": cost_pct,
            "signals": int(merged.height), "coverage": coverage,
            "scan_days": len([d for d in calendar if start <= d <= end]),
            "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": "阶段A分批扫描后并集去重的信号池；组合评估不重跑策略。",
        }
        mtmp = folder / "meta.json.tmp"
        mtmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        os.replace(mtmp, folder / "meta.json")
        with _POOL_LOCK:
            _STATE.update({"pool": merged, "meta": meta, "source": "strategy_combo",
                           "mtime": folder.joinpath("pool.parquet").stat().st_mtime})
        report(100, f"完成：信号池 {merged.height} 条")
        return {"signals": int(merged.height), "usable": len(usable), "excluded": total - len(usable)}

    def _merge_frames(self, frames: list[pl.DataFrame]) -> pl.DataFrame:
        """多批结果按 (signal_date, symbol) 并集去重，strategy_ids 取并集，收益取首份。"""
        if not frames:
            return empty_frame()
        pool: dict[tuple[str, str], dict] = {}
        for frame in frames:
            for row in frame.iter_rows(named=True):
                d = _is_date(row["signal_date"])
                key = (d.isoformat(), row["symbol"])
                ids = set(x for x in str(row["strategy_ids"] or "").split(",") if x)
                if key not in pool:
                    rec = dict(row)
                    rec["strategy_ids"] = ids
                    pool[key] = rec
                else:
                    pool[key]["strategy_ids"] |= ids
        cols = list(_SCHEMA)
        rows = []
        for (d, sym), rec in pool.items():
            ids = sorted(rec["strategy_ids"])
            rec["strategy_ids"] = ",".join(ids)
            rec["strategy_count"] = len(ids)
            rows.append({c: rec.get(c) for c in cols})
        return pl.from_dicts(rows, schema=_SCHEMA).sort(
            ["signal_date", "symbol"], descending=[True, False]
        ) if rows else empty_frame()

    def submit_scan(self, *, strategy_ids: list[str], start: date, end: date,
                    cost_pct: float = DEFAULT_COST_PCT, force: bool = False) -> dict:
        store = _combo_job_store(self.data_dir)
        job_id, is_new = store.create(timeout_s=SCAN_JOB_TIMEOUT_S)
        if not is_new:
            return {"job_id": job_id, "reused": True, "status": store.get(job_id).get("status")}
        ids = list(dict.fromkeys(strategy_ids))

        def progress(pct: int, msg: str) -> None:
            store.progress(job_id, "scan", pct, msg, skip_log=True)

        def worker() -> None:
            store.start(job_id)
            try:
                with _SCAN_LOCK:
                    result = self.run_scan(
                        strategy_ids=ids, start=start, end=end, cost_pct=cost_pct,
                        force=force, progress=progress, job_id=job_id,
                    )
                store.succeed(job_id, result)
            except Exception as exc:  # noqa: BLE001
                logger.exception("strategy_combo: 扫描失败")
                store.fail(job_id, str(exc))

        threading.Thread(target=worker, daemon=True, name=f"strategy-combo-{job_id}").start()
        return {"job_id": job_id, "reused": False, "status": "pending"}

    def job_status(self, job_id: str) -> dict | None:
        return _combo_job_store(self.data_dir).get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        return request_cancel(job_id)

    # ── 查询门面 ────────────────────────────────────────────────
    def rankings(self, **kwargs) -> dict:
        pool, meta, source, computed = self.pool_state()
        if not computed or pool.height == 0:
            return {"computed": False, "meta": {**meta, "source": source},
                    "overall": {}, "rows": [], "all_rows": []}
        result = evaluate(pool, **kwargs)
        result["computed"] = True
        result["meta"] = {**meta, **result["meta"]}
        return result

    def heatmap(self, *, horizon: int = HORIZON_DEFAULT,
                max_strategies: int = MAX_POOL_STRATEGIES) -> dict:
        pool, meta, source, computed = self.pool_state()
        if not computed or pool.height == 0:
            return {"computed": False, "strategies": [], "cells": [], "horizon": horizon}
        out = pair_heatmap(pool, horizon=horizon, max_strategies=max_strategies)
        out["computed"] = True
        return out

    def combo_signals(self, *, key: str, horizon: int = HORIZON_DEFAULT,
                      result: str = "all", sort: str = "signal_date", order: str = "desc",
                      offset: int = 0, limit: int = 24) -> dict:
        pool, meta, source, computed = self.pool_state()
        if not computed or pool.height == 0:
            return {"computed": False, "total": 0, "rows": [], "horizon": horizon}
        hit = combo_signal_ids(pool, key)
        idx = np.nonzero(hit)[0]
        sub = pool[idx.tolist()] if len(idx) else pool.head(0)
        status_col = f"status_{horizon if horizon in HORIZONS else HORIZON_DEFAULT}"
        ret_col = f"ret_{horizon if horizon in HORIZONS else HORIZON_DEFAULT}"
        if result in ("win", "lose", "pending"):
            sub = sub.filter(pl.col(status_col) == result)
        sort_key = sort if sort in {"signal_date", "strategy_count", "ret_1", "ret_3", "ret_5"} else "signal_date"
        sub = sub.sort([sort_key, "signal_date", "symbol"],
                       descending=[order != "asc", True, False], nulls_last=True)
        total = sub.height
        page = sub.slice(offset, limit)

        # 缩略图复用复盘服务的 _page_bars（只读 repo，不重跑策略）
        try:
            bars_by_id = self._review_service()._page_bars(page)  # noqa: SLF001
        except Exception:
            logger.warning("strategy_combo: 缩略图读取失败", exc_info=True)
            bars_by_id = {}

        rows = []
        for r in page.iter_rows(named=True):
            item = {
                "id": r["id"], "signal_date": _is_date(r["signal_date"]).isoformat(),
                "symbol": r["symbol"], "name": r["name"] or "",
                "strategy_ids": r["strategy_ids"].split(",") if r["strategy_ids"] else [],
                "strategy_count": int(r["strategy_count"]),
                "entry_close": round(float(r["entry_close"]), 4) if r["entry_close"] else None,
                "tradable": r["tradable"], "bars": bars_by_id.get(r["id"], []),
            }
            for h in HORIZONS:
                item[f"ret_{h}"] = r[f"ret_{h}"]
                item[f"status_{h}"] = r[f"status_{h}"]
                item[f"exit_date_{h}"] = (
                    _is_date(r[f"exit_date_{h}"]).isoformat() if r[f"exit_date_{h}"] is not None else None
                )
            rows.append(item)
        return {"computed": True, "total": total, "offset": offset, "limit": limit,
                "horizon": horizon, "result": result, "key": key, "rows": rows}
