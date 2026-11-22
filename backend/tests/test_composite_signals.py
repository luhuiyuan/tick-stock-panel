"""原子/复合信号分层: 校验、编译内联、days 偏移、未知传播、依赖提取。"""
from datetime import date

import polars as pl
import pytest

from app.strategy import custom_signals as cs


def atom(sid, left="close", op=">", right="0", days=0):
    return {
        "id": sid, "name": sid, "kind": "entry", "timeframe": "daily",
        "conditions": [{"left": left, "op": op, "right": right,
                        "leftDays": days, "rightDays": 0}],
    }


def composite(sid, expr):
    return {
        "id": sid, "name": sid, "kind": "entry", "timeframe": "daily",
        "type": "composite", "expr": expr,
    }


CATALOG = {
    "a_up": atom("a_up", "close", ">", "0"),
    "a_down": atom("a_down", "close", "<", "0"),
}


# ── 校验 ────────────────────────────────────────────────
def test_validate_composite_ok():
    cs.validate(composite("c1", {"op": "and", "args": [
        {"ref": "a_up", "days": 0}, {"ref": "a_down", "days": 1}]}))


def test_validate_composite_rejects_conditions():
    sig = composite("c1", {"ref": "a_up"})
    sig["conditions"] = [{"left": "close", "op": ">", "right": "0"}]
    with pytest.raises(ValueError, match="不能写 conditions"):
        cs.validate(sig)


def test_validate_composite_rejects_bad_op():
    with pytest.raises(ValueError, match="op 必须是"):
        cs.validate(composite("c1", {"op": "xor", "args": [{"ref": "a_up"}, {"ref": "a_down"}]}))


def test_validate_composite_not_needs_one_arg():
    with pytest.raises(ValueError, match="not 需恰好 1 个参数"):
        cs.validate(composite("c1", {"op": "not", "args": [{"ref": "a_up"}, {"ref": "a_down"}]}))


def test_validate_composite_days_range():
    with pytest.raises(ValueError, match="days 必须在"):
        cs.validate(composite("c1", {"ref": "a_up", "days": 999}))


def test_validate_atom_default_type_unchanged():
    # 存量信号（无 type 字段）仍是原子, 校验路径不变
    cs.validate(atom("legacy"))


# ── 引用提取 / 最大偏移 ─────────────────────────────────
def test_expr_refs_and_max_days():
    expr = {"op": "and", "args": [
        {"ref": "a_up", "days": 1},
        {"op": "or", "args": [{"ref": "a_down", "days": 3}, {"ref": "a_up", "days": 0}]},
    ]}
    assert cs.expr_refs(expr) == {"a_up", "a_down"}
    assert cs.expr_max_days(expr) == 3


# ── 编译内联 ────────────────────────────────────────────
def _frame():
    return pl.DataFrame({
        "symbol": ["X"] * 5,
        "date": [date(2026, 1, i) for i in range(1, 6)],
        "close": [1.0, 2.0, -1.0, 3.0, -2.0],
    })


def test_build_composite_and():
    sig = composite("c_and", {"op": "and", "args": [
        {"ref": "a_up", "days": 0}, {"ref": "a_down", "days": 0}]})
    exprs = cs.build_expressions([sig], catalog=CATALOG)
    out = _frame().with_columns(exprs["csg_c_and"].alias("flag"))
    # close>0 且 close<0 永不同真 → 全 False
    assert out["flag"].to_list() == [False] * 5


def test_build_composite_or():
    sig = composite("c_or", {"op": "or", "args": [
        {"ref": "a_up", "days": 0}, {"ref": "a_down", "days": 0}]})
    exprs = cs.build_expressions([sig], catalog=CATALOG)
    out = _frame().with_columns(exprs["csg_c_or"].alias("flag"))
    assert out["flag"].to_list() == [True, True, True, True, True]


def test_build_composite_not():
    sig = composite("c_not", {"op": "not", "args": [{"ref": "a_up", "days": 0}]})
    exprs = cs.build_expressions([sig], catalog=CATALOG)
    out = _frame().with_columns(exprs["csg_c_not"].alias("flag"))
    assert out["flag"].to_list() == [False, False, True, False, True]


def test_build_composite_days_shift():
    # 引用昨日的 a_up(close>0): 昨日收盘>0 则今日 flag True
    sig = composite("c_lag", {"ref": "a_up", "days": 1})
    exprs = cs.build_expressions([sig], catalog=CATALOG)
    out = _frame().with_columns(exprs["csg_c_lag"].alias("flag"))
    assert out["flag"].to_list() == [None, True, True, False, True]


def test_composite_inlines_atom_fields_in_dependencies(monkeypatch):
    sig = composite("c_dep", {"op": "and", "args": [
        {"ref": "a_up", "days": 0}, {"ref": "a_down", "days": 2}]})
    monkeypatch.setattr(cs, "_catalog_cache", CATALOG)
    deps = cs._definition_dependencies(sig)
    # 内联后根字段应含 close（原子的字段）, 已内联无需 csg_ 列
    assert "close" in deps


def test_unknown_atom_propagates():
    # 原子带 data_contract 且窗口不足 → guard 置 null → 复合应 unknown(null)
    atom_gap = atom("a_gap", "close", ">", "0")
    atom_gap["data_contract"] = {"version": 1, "inputs": {"close": 3}, "ohlc_window": 0}
    from app.strategy.strict_signal_data import attach_calendar
    from app.services.surge_coverage import Coverage
    from pathlib import Path
    from tempfile import TemporaryDirectory
    # 用足够日历附着, 前两行 warmup 不足应为 null
    days = [date(2026, 1, i) for i in range(1, 6)]
    df = attach_calendar(_frame(), days)
    cat = {**CATALOG, "a_gap": atom_gap}
    sig = composite("c_unk", {"op": "and", "args": [
        {"ref": "a_gap", "days": 0}, {"ref": "a_up", "days": 0}]})
    exprs = cs.build_expressions([sig], catalog=cat)
    out = df.with_columns(exprs["csg_c_unk"].alias("flag"))
    # 前两行因 warmup(3) 不足 → null; 之后按 close>0 判定
    assert out["flag"].to_list()[:2] == [None, None]
    assert out["flag"].to_list()[2] is False   # close=-1 <0


def test_missing_ref_atom_is_skipped():
    sig = composite("c_missing", {"ref": "does_not_exist"})
    exprs = cs.build_expressions([sig], catalog=CATALOG)
    assert "csg_c_missing" not in exprs
