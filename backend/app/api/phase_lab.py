"""/phase-lab「价格结构识别」页的后端。

两个接口：
- ``GET /timeline``：把 vendored ``hot_money_radar`` 的 26 条形态规则，在
  **指定标的 + 指定周期**上逐日 PIT 重放，返回每日命中形态与主导阶段，供前端
  先看「算法算出了什么」。本模块**不含任何形态判定逻辑**：判定全部调用
  ``radar_core.match_patterns`` / ``radar_core._pattern_phase``，与
  ``backend/tests/test_hot_money_radar_vendor.py`` 的逐字一致性验证是同一份实现。
- ``GET /evaluate``：方向 A 的客观基准评测。标准答案 = 对展示区间全量日线跑
  离线最优分割（**明确用到未来数据，仅作基准**），再给三个 PIT 实时算法用
  F1@容差 / covering / 确认延迟打分。计算在 ``app.services.structure_eval``。

注意：timeline 的「主导阶段」是选股信号命中日的汇总标签，**不是价格结构识别**；
上一轮诊断已证明它在单边下跌里会大量误标。页面已改定位为「价格结构识别」，
并以 evaluate 的客观基准为准，timeline 仅作为待检验算法之一保留。

口径（见 docs/主力行为阶段识别与经验库-功能方案.md 最终方案）：
- 数据源：本地 enriched 日线分区（20 年，2006-11-13 起），不用在线行情；
- 周期：近 60 / 250 / 500 / 1000 交易日，默认 250；每个展示日额外需要
  WARMUP=184 根暖机（P21 的 PIT 窗口需 185 根），窗口 = bars[:i+1]；
- P1 大盘门控关闭（依赖上游 510310 指数净值，本地无）；
- 换手率：本地 ``turnover_rate`` 与 ``instruments.float_shares`` 均为空，
  不伪造、不估算，``turnover`` 传 None 让上游走自己的兜底；P8/P10/P13/P26
  因此退化，页面如实标注。
- 上游复测结论是上游仓库的结论，**未经本系统复测**，仅作展示。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import polars as pl
from fastapi import APIRouter, HTTPException, Query, Request

from app.indicators.vendor.hot_money_radar import adapter, radar_core
from app.services import structure_eval as se

router = APIRouter(prefix="/api/phase-lab", tags=["phase-lab"])

LOOKBACKS: tuple[int, ...] = (60, 250, 500, 1000)
DEFAULT_LOOKBACK = 250
RULE_MODES = ("all", "effective")
WARMUP = 184
# 依赖换手率 / 筹码分布、本地数据无法提供的 4 条规则（见 SOURCE.md 数据契约）
CHIP_DEGRADED_RULES: tuple[str, ...] = ("P8", "P10", "P13", "P26")
CACHE_TTL_S = 60.0

CHIP_DEGRADED_NOTE = (
    "本地 enriched 日线的 turnover_rate 与 instruments.float_shares 均为空，"
    "无法补算换手率。P8/P10/P13/P26 依赖换手率或筹码分布，按上游兜底逻辑退化；"
    "此处不伪造、不估算缺失值，其余 22 条规则正常参与。"
)

# 每条规则的「触发条件摘要」与「上游复测结论」。文案逐字摘自上游
# stock_hot_money_radar.py 的 PATTERN_DESC（MIT，快照 b26f01cc），仅做摘录，
# 不参与任何判定。retest 是上游在其自有双池上的结论，未经本系统复测。
_PATTERN_NOTES: dict[str, tuple[str, str]] = {
    "P1": (
        "低位缩量企稳两日首次确认，或低位首次突破前10日最高收盘并通过510310日终风险门",
        "正向：全历史复测双池10日胜率54.79%/57.56%、同池超额+0.464%/+1.297%、HAC t=3.21/3.24；小盘更有效，进入吸筹分10%",
    ),
    "P2": (
        "位置<0.40 + 近10日真实长下影≥2根(下影≥2×实体且≥2%) + 当日量能/此前20日均量0.4~1.0",
        "正向：全历史复测双池10/20日正超额；大盘更有效，进入吸筹分5%",
    ),
    "P3": (
        "位置<0.32 + 近10日收盘跌≥4.2%且量低于此前20日均量，事件后最多再破底6%，确认量比≤1.35并首次完整收复",
        "正向：全历史复测双池命中率0.1005%/0.2002%，2~40日超额均为正",
    ),
    "P4": (
        "位置<0.60 + 近5日/前20日量比1.2~2.5 + |20日涨跌|<6% + CMF>0，仅在核心状态首次进入时报告",
        "中性/未验证：双池留出未验证为买点，保留中性结构观察，不驱动绿色吸筹阶段",
    ),
    "P5": (
        "位置<0.45 + 两摆动低点(-2%~+10%) + 颈线反弹≥5% + 二底后8日内；今日由未突破切换为突破前3日收盘高点",
        "正向（产品口径）：双池10日胜率55.2%/63.8%、同池超额+0.35%/+0.84%；按产品决定暂时纳入核心有效集",
    ),
    "P6": (
        "近8日长上影(>3%且>2×实体)创20日新高后收盘缩回：探顶又压回",
        "仅观测：保留形态观测，不作为有效因子",
    ),
    "P7": (
        "位置<0.40 + 量比>1.5 + 近5日最大振幅>7%：低位突然放量异动",
        "仅观测：保留形态观测，不作为有效因子",
    ),
    "P8": (
        "站上MA20 + 回踩近10日高点-2%~-15% + 跌日量<涨日量 + 筹码集中≥0.40：挖坑不破位",
        "上游未给统一复测结论；依赖筹码分布，本地因无换手率数据退化为不参与",
    ),
    "P9": (
        "多头排列(MA5>10>20) + 近8日涨跌切换≥4次 + 低点抬高：边拉边洗",
        "上游未给统一复测结论",
    ),
    "P10": (
        "量比>1.5 + 收盘>MA20 + 筹码集中≥0.45：高换手震仓但筹码峰不发散",
        "上游未给统一复测结论；依赖换手率与筹码分布，本地因无数据退化为不参与",
    ),
    "P11": (
        "右进左出触发器：从低/中位放量(>1.3×)突破近20日收盘高点",
        "负向风险：全历史逐日复测为双池负向风险",
    ),
    "P12": (
        "连续涨停 streak≥2",
        "方向随周期变化：全历史逐日复测 2 日动量有效，hotmoney 池 10~40 日转为反转风险",
    ),
    "P13": (
        "今日首板(近20日无涨停) + 换手10%~45%",
        "方向随周期变化：全历史逐日复测 2 日动量有效，20~40 日转为反转风险；依赖换手率，本地退化为不参与",
    ),
    "P14": (
        "位置≥0.85 + 量比>1.5 + 近5日涨幅≤2% + 上影>2%：高位放量不涨",
        "风控有效：全历史逐日复测双池2~40日风控有效",
    ),
    "P15": (
        "前15日内曾出现旧P15新高量背离，当前较近10日盘中高点回撤≥4%，且当日量能/此前20日均量>1.5；信号记在确认日",
        "负向：全历史复测双池2~40日胜率<50%且同池超额均为负",
    ),
    "P16": (
        "位置≥0.80 + 近3日出现40日最大量且相对昨收下跌 + 当前较近10日最高价回撤≥3%",
        "负向：全历史复测双池2~40日胜率与同池超额均低于旧版",
    ),
    "P17": (
        "位置≥0.80 + 较前期冲高>16%后从峰值回落≤-8%，峰值首次出现于最近5根、信号日不反弹",
        "负向：全历史复测双池2~40日胜率与超额均低于旧版",
    ),
    "P18": (
        "位置≥0.80 + 昨阳今阴且今实体完全吞没昨实体：顶部看跌吞没",
        "上游未给统一复测结论（仅作形态观察）",
    ),
    "P19": (
        "位置≥0.70 + 量比>1.8 + 实体跌>6%且收在当日价区下1/4：巨量灌压大阴",
        "风控有效／负向：全历史逐日复测双池2~40日持续负超额",
    ),
    "P20": (
        "收盘跌破MA20(第5根bar仍在当时MA20上方) + 近5/此前20日量能比>1.3 + 当日跌≥1%",
        "风控有效／负向：全历史复测双池2~40日胜率<50%且负超额",
    ),
    "P21": (
        "近5日最低价深破前40日箱底≥2.5%，当前收盘重新站回，且信号日量能≤此前20日均值0.75倍：Wyckoff spring",
        "正向：全历史复测双池10/20/40日胜率与同池超额均为正，进入吸筹分10%",
    ),
    "P22": (
        "今日盘中破前40日高但收盘没站上 + 当日放量>1.8×：放量假突破(高位拒绝)",
        "风控有效：全历史逐日复测双池2~40日风控有效",
    ),
    "P23": (
        "位置<0.36 + 近20/60日振幅比<0.96 + 收盘≥1.005×MA20，结构状态首次成立且量比0.4~1.2",
        "正向（产品口径）：冻结快照复测双池10日胜率51.83%/52.19%、同池超额+0.331%/+0.529%；按产品口径纳入核心有效集",
    ),
    "P24": (
        "位置<0.50 + 30日涨幅≤3% + 量纲化OBV>0.10 + OBV价差>0.25，连续5日后仅首次确认",
        "正向：最新复测双池10日有效",
    ),
    "P25": (
        "120日价格分位≤65% + 60日振幅≤35%且涨跌≤16% + 近20日/前40日均量≤70% + 量能斜率≤-0.5% + 20日涨幅≥0.5% + 距20日平台≤4%",
        "正向（产品口径）：全历史复测双池10日胜率52.4%/53.7%、同池超额+0.24%/+0.55%；leader HAC未显著，按产品口径纳入核心有效集",
    ),
    "P26": (
        "获利盘比例≥90%，或5个交易日前<40%且当前≥60%；同时要求当日量能≥此前20日均量2倍、近5日涨幅≥5%、较近10日盘中高点回撤≥3%",
        "负向：全历史复测双池2~40日胜率均<50%且同池超额均为负；依赖筹码分布，本地退化为不参与",
    ),
}

# 同参短 TTL 缓存：一次请求要扫 4857 个 parquet 分区，重复查询不必重扫。
_CACHE: dict[tuple[str, int, str], tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()


def _cache_get(key: tuple[str, int, str], producer: Callable[[], dict]) -> dict:
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and now - hit[0] < CACHE_TTL_S:
            return hit[1]
    result = producer()
    with _CACHE_LOCK:
        _CACHE[key] = (now, result)
    return result


def _code_of(symbol: str) -> str:
    """上游 ``code`` 是 6 位纯代码（见一致性测试）；项目 symbol 带交易所后缀。"""
    return symbol.split(".", 1)[0]


def _normalize_symbol(raw: str) -> str:
    sym = (raw or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="股票代码不能为空")
    return sym


def _load_symbol_rows(data_dir: Path, symbol: str) -> list[dict[str, Any]]:
    """逐分区读 enriched 日线，按 symbol 过滤并排序。

    只取规则需要的列；``turnover_rate`` 本地全空时保留 None，不估算。
    """
    from app.tickflow.repository import enriched_dirname

    root = Path(data_dir) / enriched_dirname("stock")
    if not root.exists():
        raise HTTPException(status_code=404, detail="本地没有 enriched 日线数据")
    glob = str(root / "**" / "*.parquet")
    # 输入不带后缀时兼容 6 位代码；带后缀时精确匹配。
    expr = (
        pl.col("symbol") == symbol
        if "." in symbol
        else pl.col("symbol").str.starts_with(symbol + ".")
    )
    try:
        frame = (
            pl.scan_parquet(glob, hive_partitioning=True)
            .filter(expr)
            .select(
                [
                    "symbol",
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "amount",
                    "turnover_rate",
                ]
            )
            .sort("date")
            .collect()
        )
    except Exception as exc:  # noqa: BLE001 — 分区损坏/缺失时给可读错误
        raise HTTPException(status_code=500, detail=f"读取 enriched 日线失败: {exc}") from exc
    if frame.is_empty():
        raise HTTPException(status_code=404, detail=f"本地没有该标的的 enriched 日线: {symbol}")
    return frame.to_dicts()


def _with_chg(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """补上游 ``chg``（百分数，如 9.9 表示 +9.9%）。

    本地 enriched 没有涨跌幅列，用相邻收盘价推算；复权价在除权日会有偏差，
    这是本地数据能给出的最接近上游 ``daily_change_pct`` 的值。不补会让依赖
    ``bars[i]['chg']`` 的规则在 match_patterns 的 try/except 里静默跳过。
    """
    out: list[dict[str, Any]] = []
    prev_close: float | None = None
    for row in rows:
        item = dict(row)
        close = item.get("close")
        close_f = float(close) if close is not None else None
        if close_f is not None and prev_close is not None and prev_close > 0:
            item["chg"] = (close_f / prev_close - 1.0) * 100.0
        else:
            item["chg"] = None
        out.append(item)
        if close_f is not None:
            prev_close = close_f
    return out


def _is_missing(value: Any) -> bool:
    """None / 非数值 / NaN 都算缺失；0 是有效值（不视为缺失）。"""
    if value is None:
        return True
    try:
        f = float(value)
    except (TypeError, ValueError):
        return True
    return f != f  # NaN


def _selected_patterns(rules: str) -> tuple | None:
    if rules == "all":
        return None
    return tuple(p for p in radar_core.PATTERNS if p[0] in radar_core.PATTERN_EFFECTIVE)


def _decorate(fired: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in fired:
        reason, retest = _PATTERN_NOTES.get(item["code"], ("", "上游未提供说明"))
        out.append({**item, "reason": reason, "retest": retest})
    return out


def build_timeline(
    rows: Sequence[Mapping[str, Any]],
    symbol: str,
    lookback: int = DEFAULT_LOOKBACK,
    rules: str = "all",
) -> dict[str, Any]:
    """纯函数：逐日 PIT 重放，返回时间轴。测试直接调用它，不依赖 FastAPI/repo。

    第 d 个展示日只用 ``date <= d`` 的 bar（``window = bars[:i+1]``），
    不引入未来数据；判定与 ``radar_core.match_patterns`` 完全一致。
    """
    if lookback not in LOOKBACKS:
        raise ValueError(f"lookback 只支持 {LOOKBACKS}")
    if rules not in RULE_MODES:
        raise ValueError(f"rules 只支持 {RULE_MODES}")

    enriched = _with_chg(rows)
    need = WARMUP + lookback
    window_rows = list(enriched[-need:]) if len(enriched) > need else list(enriched)
    bars = adapter.rows_to_bars(window_rows)
    code = _code_of(symbol)
    patterns = _selected_patterns(rules)
    chip_degraded = all(_is_missing(row.get("turnover_rate")) for row in window_rows)

    timeline: list[dict[str, Any]] = []
    for i in range(WARMUP, len(bars)):
        window = bars[: i + 1]
        raw = radar_core.match_patterns(code, window, patterns=patterns)
        last = window[-1]
        date_value = last["date"]
        timeline.append(
            {
                "date": str(date_value)[:10],
                "ohlcv": {
                    "open": last["open"],
                    "high": last["high"],
                    "low": last["low"],
                    "close": last["close"],
                    "volume": last["volume"],
                    "amount": last["amount"],
                    "chg": last["chg"],
                    "turnover": last["turnover"],
                },
                "fired": _decorate(raw),
                "phase": radar_core._pattern_phase(raw),
                "chip_degraded": chip_degraded,
            }
        )

    return {
        "symbol": symbol,
        "lookback": lookback,
        "rules": rules,
        "warmup": WARMUP,
        "rule_count": len(patterns) if patterns is not None else len(radar_core.PATTERNS),
        "from_date": timeline[0]["date"] if timeline else None,
        "to_date": timeline[-1]["date"] if timeline else None,
        "total_days": len(timeline),
        "hit_days": sum(1 for t in timeline if t["fired"]),
        "chip_degraded": chip_degraded,
        "degraded_rules": list(CHIP_DEGRADED_RULES),
        "degraded_note": CHIP_DEGRADED_NOTE,
        "timeline": timeline,
    }


@router.get("/timeline")
def timeline(
    request: Request,
    symbol: str = Query(..., description="标的代码，如 000006.SZ"),
    lookback: int = Query(DEFAULT_LOOKBACK, description="近 N 个交易日：60/250/500/1000"),
    rules: str = Query("all", pattern="^(all|effective)$", description="all=26 条；effective=19 条核心有效集"),
) -> dict:
    """逐日 PIT 重放形态命中，供 /phase-lab 可视化确认算法输出。"""
    sym = _normalize_symbol(symbol)
    if lookback not in LOOKBACKS:
        raise HTTPException(status_code=400, detail=f"lookback 只支持 {LOOKBACKS}")
    repo = request.app.state.repo
    data_dir = Path(getattr(getattr(repo, "store", None), "data_dir", "."))

    def produce() -> dict:
        rows = _load_symbol_rows(data_dir, sym)
        return build_timeline(rows, sym, lookback=lookback, rules=rules)

    return _cache_get((sym, lookback, rules), produce)


def _eval_rows_from_timeline(
    timeline: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """timeline 每日 ohlcv → structure_eval 的展示区间行（逐日对齐）。

    缺列用当日收盘价兜底（不影响离线分割），close 缺失则跳过该日。
    """
    rows: list[dict[str, Any]] = []
    for day in timeline:
        o = day["ohlcv"]
        close = o.get("close")
        if close is None:
            continue

        def pick(key: str, fallback: Any) -> Any:
            value = o.get(key)
            return value if value is not None else fallback

        rows.append(
            {
                "date": day["date"],
                "open": pick("open", close),
                "high": pick("high", close),
                "low": pick("low", close),
                "close": close,
                "volume": pick("volume", 0.0),
            }
        )
    return rows


@router.get("/evaluate")
def evaluate(
    request: Request,
    symbol: str = Query(..., description="标的代码，如 000006.SZ"),
    lookback: int = Query(DEFAULT_LOOKBACK, description="近 N 个交易日：60/250/500/1000"),
    pen: float = Query(
        se.DEFAULT_PEN,
        description="离线分割惩罚：0.02/0.05/0.1/0.3；越大分段越粗",
    ),
) -> dict:
    """方向 A：先用未来数据算客观价格结构基准，再给 PIT 实时算法打分。

    标准答案**明确用到整段未来数据，仅作检验基准，不可用于实盘**；
    三个被检验算法逐日只用当日及以前数据。详见 ``structure_eval`` 的 notes。
    """
    sym = _normalize_symbol(symbol)
    if lookback not in LOOKBACKS:
        raise HTTPException(status_code=400, detail=f"lookback 只支持 {LOOKBACKS}")
    if pen not in se.PEN_OPTIONS:
        raise HTTPException(status_code=400, detail=f"pen 只支持 {se.PEN_OPTIONS}")
    repo = request.app.state.repo
    data_dir = Path(getattr(getattr(repo, "store", None), "data_dir", "."))

    def produce() -> dict:
        started = time.perf_counter()
        # 复用 timeline 的同参 60s 缓存：页面通常先看 timeline，避免重复扫分区 + 重放
        tl = _cache_get(
            (sym, lookback, "all"),
            lambda: build_timeline(
                _load_symbol_rows(data_dir, sym), sym, lookback=lookback, rules="all"
            ),
        )
        rows = _eval_rows_from_timeline(tl["timeline"])
        hit_dates = [d["date"] for d in tl["timeline"] if d["fired"]]
        out = se.evaluate(rows, sym, pen=pen, radar_hit_dates=hit_dates)
        out["lookback"] = lookback
        out["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return out

    # 用带前缀的 key 与 timeline 缓存隔离，pen 也参与 key
    return _cache_get((f"eval:{sym}", lookback, f"pen={pen}"), produce)


# ---- 走势结构视图：只用未来数据的离线基准，不做逐日重放 -------------------
STRUCTURE_NOTES: tuple[str, ...] = (
    "本页只用展示区间的全量日线（含未来数据）跑一次离线最优分割，得到客观走势结构；"
    "它是离线结构标注，不涉及任何实时判断算法。",
    "标准答案 = 对 log(close) 跑 ruptures_lite.Pelt(model=l2, min_size=15, jump=1) "
    "的全局最优分割；明确用到整段未来数据，仅作离线结构标注，不可用于实盘。",
    "这是「给定算法 + pen 参数下的最优分段」，不是绝对真理；pen 越大分段越粗，"
    "切换 pen 后变点位置与段数都会变。",
    "段方向按每段首尾收盘价涨跌 ±5% 分为 UP / DOWN / SIDEWAYS，只用于着色展示，"
    "不做主观「吸筹 / 洗盘 / 出货」阶段判定。",
)


def _num(value: Any, default: float) -> float:
    """数值兜底：None / 非数值 / NaN 一律用默认值，保证 JSON 可序列化。"""
    try:
        result = float(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    return result if result == result else default


def build_structure(
    rows: Sequence[Mapping[str, Any]],
    symbol: str,
    *,
    lookback: int = DEFAULT_LOOKBACK,
    pen: float = se.DEFAULT_PEN,
    direction_threshold: float = se.DEFAULT_DIRECTION_THRESHOLD,
) -> dict[str, Any]:
    """纯函数：只用展示区间全量日线（含未来数据）算客观走势结构。

    与 ``build_timeline`` / ``evaluate`` 完全解耦：不调用 ``radar_core``、
    不逐日重放、不评分，只做一次离线 Pelt 全局最优分割。返回 K 线 bars
    与 ``ground_truth`` 的索引逐日对齐，前端可直接画色带与变点。
    """
    if lookback not in LOOKBACKS:
        raise ValueError(f"lookback 只支持 {LOOKBACKS}")
    if pen not in se.PEN_OPTIONS:
        raise ValueError(f"pen 只支持 {se.PEN_OPTIONS}")
    if direction_threshold < 0:
        raise ValueError("direction_threshold 不能为负")

    window = list(rows[-lookback:]) if len(rows) > lookback else list(rows)
    dates: list[str] = []
    closes: list[float] = []
    bars: list[dict[str, Any]] = []
    for row in window:
        raw = row.get("close")
        try:
            close = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            close = None
        # 与 se._series 同口径：缺失 / NaN / 非正收盘的行整行丢弃
        if close is None or close != close or close <= 0:
            continue
        date = str(row.get("date"))[:10]
        dates.append(date)
        closes.append(close)
        bars.append(
            {
                "date": date,
                "open": _num(row.get("open"), close),
                "high": _num(row.get("high"), close),
                "low": _num(row.get("low"), close),
                "close": close,
                "volume": _num(row.get("volume"), 0.0),
            }
        )

    gt = se.ground_truth(
        dates, np.asarray(closes, dtype=float),
        pen=pen, direction_threshold=direction_threshold,
    )
    return {
        "symbol": symbol,
        "lookback": lookback,
        "pen": pen,
        "direction_threshold": direction_threshold,
        "pen_options": list(se.PEN_OPTIONS),
        "from_date": dates[0] if dates else None,
        "to_date": dates[-1] if dates else None,
        "n_obs": len(dates),
        "dates": dates,
        "bars": bars,
        "ground_truth": gt,
        "notes": list(STRUCTURE_NOTES),
    }


@router.get("/structure")
def structure(
    request: Request,
    symbol: str = Query(..., description="标的代码，如 000006.SZ"),
    lookback: int = Query(DEFAULT_LOOKBACK, description="近 N 个交易日：60/250/500/1000"),
    pen: float = Query(
        se.DEFAULT_PEN,
        description="离线分割惩罚：0.02/0.05/0.1/0.3；越大分段越粗",
    ),
    direction_threshold: float = Query(
        se.DEFAULT_DIRECTION_THRESHOLD,
        description="段方向阈值，默认 0.05（首尾涨跌 ±5%）",
    ),
) -> dict:
    """只用未来数据算客观走势结构（离线 Pelt 全局最优分割）。

    不逐日重放、不检验实时算法、不评分；整段日线一次性喂给离线分割器。
    标准答案明确用到未来数据，仅作离线结构标注，不可用于实盘。
    """
    sym = _normalize_symbol(symbol)
    if lookback not in LOOKBACKS:
        raise HTTPException(status_code=400, detail=f"lookback 只支持 {LOOKBACKS}")
    if pen not in se.PEN_OPTIONS:
        raise HTTPException(status_code=400, detail=f"pen 只支持 {se.PEN_OPTIONS}")
    if direction_threshold < 0:
        raise HTTPException(status_code=400, detail="direction_threshold 不能为负")
    repo = request.app.state.repo
    data_dir = Path(getattr(getattr(repo, "store", None), "data_dir", "."))

    def produce() -> dict:
        started = time.perf_counter()
        rows = _load_symbol_rows(data_dir, sym)
        try:
            out = build_structure(
                rows, sym, lookback=lookback, pen=pen,
                direction_threshold=direction_threshold,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        out["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return out

    return _cache_get(
        (f"structure:{sym}", lookback, f"pen={pen}|th={direction_threshold}"),
        produce,
    )
