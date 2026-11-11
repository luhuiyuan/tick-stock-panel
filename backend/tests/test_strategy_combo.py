"""策略组合探索：命中矩阵枚举、位掩码一致性、统计口径、IS/OOS 切分、评分与合并去重。

口径见 app/services/strategy_combo.py 模块说明。这些用例只测纯函数，构造内存信号池，
不依赖行情数据；真实信号池的端到端回归由运行后的接口核对完成。
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.services.strategy_combo import (
    HORIZONS,
    IS_RATIO_DEFAULT,
    PLR_CAP,
    RESONANCE_RULES,
    build_arrays,
    combo_signal_ids,
    enumerate_combos,
    evaluate,
    pair_heatmap,
    pool_strategies,
    _hit,
    _stat,
)
from app.services.strategy_review import _SCHEMA, _stats


def _row(day: date, symbol: str, sids: list[str], rets: tuple[float, float, float]) -> dict:
    rec: dict = {
        "id": f"{day.isoformat()}|{symbol}",
        "signal_date": day,
        "symbol": symbol,
        "name": f"N{symbol[:2]}",
        "strategy_ids": ",".join(sids),
        "strategy_count": len(sids),
        "entry_close": 10.0,
        "tradable": "ok",
    }
    for h in HORIZONS:
        r = rets[HORIZONS.index(h)]
        rec[f"ret_{h}"] = r
        rec[f"exit_close_{h}"] = 10.0 * (1 + r + 0.0015)
        rec[f"exit_date_{h}"] = day + timedelta(days=h)
        rec[f"status_{h}"] = "win" if r > 0 else "lose"
    return rec


def _pool(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=_SCHEMA, strict=False)


def _fixture() -> pl.DataFrame:
    """10 个交易日 × 若干信号，策略 A/B/C 命中组合可控。"""
    rows: list[dict] = []
    base = date(2026, 1, 5)
    for d in range(10):
        day = base + timedelta(days=d)
        # A+B 双命中：胜多
        rows.append(_row(day, f"{d:06d}.SZ", ["A", "B"], (0.02, 0.01, 0.03)))
        # 仅 A：输多
        rows.append(_row(day, f"{d + 100:06d}.SZ", ["A"], (-0.01, -0.02, -0.01)))
        # A+B+C 三命中：胜
        rows.append(_row(day, f"{d + 200:06d}.SZ", ["A", "B", "C"], (0.03, 0.02, 0.01)))
        # 仅 C：赢一半（偶数日赢、奇数日输）
        rows.append(_row(day, f"{d + 300:06d}.SZ", ["C"], (0.01 if d % 2 == 0 else -0.01, 0.0, 0.0)))
    return _pool(rows)


def test_pool_strategies_sorted_and_capped():
    pool = _fixture()
    ids = pool_strategies(pool, 12)
    # A 命中最多(30)，B(20)，C(20)；返回按 id 排序
    assert ids == ["A", "B", "C"]
    assert pool_strategies(pool, 2) == ["A", "B"]  # 取命中数前 2 再排序


def test_enumerate_combos_counts_and_keys():
    combos = enumerate_combos(["A", "B", "C"], 3)
    pair_keys = {c["key"] for c in combos if c["kind"] == "combo" and c["size"] == 2}
    assert pair_keys == {"combo:A+B", "combo:A+C", "combo:B+C"}
    triple = [c for c in combos if c["kind"] == "combo" and c["size"] == 3]
    assert len(triple) == 1 and triple[0]["key"] == "combo:A+B+C"
    res = [c for c in combos if c["kind"] == "resonance"]
    assert len(res) == len(RESONANCE_RULES)
    # size 上限 2 时不含三三组合
    assert all(c["size"] <= 2 for c in enumerate_combos(["A", "B", "C"], 2) if c["kind"] == "combo")


def test_bitmask_intersection_matches_contains():
    """位掩码交集必须与离线 contains 口径逐行一致（历史 bug 回归）。"""
    pool = _fixture()
    ids = pool_strategies(pool, 12)
    arr = build_arrays(pool, 1, ids, IS_RATIO_DEFAULT)
    combos = {c["key"]: c for c in enumerate_combos(ids, 3)}
    ab = _hit(arr, combos["combo:A+B"])
    contains = (
        pool.get_column("strategy_ids").str.contains("A", literal=True)
        & pool.get_column("strategy_ids").str.contains("B", literal=True)
    ).to_numpy()
    assert np.array_equal(ab, contains)
    assert int(ab.sum()) == 20  # A+B(10) + A+B+C(10)


def test_resonance_uses_strategy_count():
    pool = _fixture()
    ids = pool_strategies(pool, 12)
    arr = build_arrays(pool, 1, ids, IS_RATIO_DEFAULT)
    combos = {c["key"]: c for c in enumerate_combos(ids, 3)}
    ge2 = _hit(arr, combos["res:>=2"])
    assert np.array_equal(ge2, (pool.get_column("strategy_count").to_numpy() >= 2))
    eq1 = _hit(arr, combos["res:==1"])
    assert np.array_equal(eq1, (pool.get_column("strategy_count").to_numpy() == 1))


def test_stat_win_rate_and_plr():
    """胜率、盈亏比、综合分口径：胜率×盈亏比×(0.5+0.5×稳定性)。"""
    pool = _fixture()
    ids = pool_strategies(pool, 12)
    arr = build_arrays(pool, 1, ids, IS_RATIO_DEFAULT)
    sel = arr["decided"]  # 全部已决
    st = _stat(arr, sel, "composite")
    assert st["n"] == 40
    # 赢: A+B(10) + A+B+C(10) + C(5) = 25, 输: 仅A(10) + C(5) = 15
    assert st["win"] == 25 and st["lose"] == 15
    assert st["win_rate"] == pytest.approx(0.625)
    assert 0 < st["profit_loss_ratio"] <= PLR_CAP + 1e-9
    # 综合分基于对外展示的 round 后字段计算，可用展示值精确反推公式
    assert st["score"] == pytest.approx(
        st["win_rate"] * min(st["profit_loss_ratio"], PLR_CAP) * (0.5 + 0.5 * st["stability"]),
        abs=1e-6,
    )
    # 排名目标切换
    assert _stat(arr, sel, "win_rate")["score"] == pytest.approx(0.625)
    assert _stat(arr, sel, "avg_ret")["score"] == pytest.approx(st["avg_ret"])
    # 空集
    assert _stat(arr, np.zeros(arr["n"], dtype=bool), "composite") is None


def test_is_oos_split_by_unique_days():
    pool = _fixture()
    ids = pool_strategies(pool, 12)
    arr = build_arrays(pool, 1, ids, 0.7)
    uniq = arr["uniq"]
    assert len(uniq) == 10
    # 分界日 = OOS 的第一个交易日(第 8 个)，IS = ords < 分界日 → 精确 70/30
    assert arr["split_date_ord"] == int(uniq[7])
    assert int(arr["is_mask"].sum()) == 28  # 前 7 天 × 4 信号
    assert int(arr["oos_mask"].sum()) == 12  # 后 3 天 × 4 信号
    assert np.array_equal(arr["is_mask"] ^ arr["oos_mask"], np.ones(arr["n"], dtype=bool))


def test_evaluate_overall_matches_review_stats():
    """全量三档统计必须与复盘页 _stats 完全一致。"""
    pool = _fixture()
    result = evaluate(pool, horizon=1)
    full = _stats(pool)
    for h in HORIZONS:
        assert result["overall"][str(h)]["win_rate"] == full[str(h)]["win_rate"]
        assert result["overall"][str(h)]["total"] == full[str(h)]["total"]
    # 终榜按 OOS 分排序，含 final_rank
    rows = result["rows"]
    assert all("final_rank" in r for r in rows)
    assert len(rows) <= result["meta"]["top_n"]
    # 全部组合覆盖两个视图
    assert len(result["all_rows"]) >= len(rows)


def test_combo_signal_ids_and_heatmap():
    pool = _fixture()
    # 下钻命中数与位掩码一致
    assert int(combo_signal_ids(pool, "combo:A+B").sum()) == 20
    assert int(combo_signal_ids(pool, "res:>=3").sum()) == 10
    assert int(combo_signal_ids(pool, "res:==1").sum()) == 20
    assert int(combo_signal_ids(pool, "combo:UNKNOWN+X").sum()) == 0
    # 热力图对称、对角为单策略
    hm = pair_heatmap(pool, horizon=1)
    assert hm["strategies"] == ["A", "B", "C"]
    assert len(hm["cells"]) == 3 and all(len(r) == 3 for r in hm["cells"])
    assert hm["cells"][0][1]["win_rate"] == hm["cells"][1][0]["win_rate"]
    assert hm["cells"][0][0]["n"] == 30  # A 单策略命中 30 条


def test_merge_frames_union_dedup():
    """多批结果按 (date, symbol) 并集去重，strategy_ids 累加，收益取首份。"""
    day = date(2026, 1, 5)
    f1 = _pool([
        _row(day, "000001.SZ", ["A"], (0.02, 0.01, 0.01)),
        _row(day, "000002.SZ", ["B"], (-0.01, -0.01, -0.01)),
    ])
    f2 = _pool([
        _row(day, "000001.SZ", ["C"], (0.02, 0.01, 0.01)),  # 同 key，策略累加
        _row(day, "000003.SZ", ["C"], (0.01, 0.01, 0.01)),
    ])
    # _merge_frames 是实例方法，用一个不依赖 repo 的最小对象调用
    from app.services.strategy_combo import StrategyComboService

    class _Stub:
        _merge_frames = StrategyComboService._merge_frames

    merged = _Stub()._merge_frames([f1, f2])  # type: ignore[attr-defined]
    assert merged.height == 3
    row = merged.filter(pl.col("symbol") == "000001.SZ").row(0, named=True)
    assert row["strategy_ids"] == "A,C"
    assert row["strategy_count"] == 2
    assert row["ret_1"] == pytest.approx(0.02)
