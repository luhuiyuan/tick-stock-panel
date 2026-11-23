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


def test_validate_composite_with_contract():
    """复合可显式声明严格契约（用于原子库：原子无契约纯积木，复合统一判定 unknown）。"""
    sig = composite("c_ct", {"op": "and", "args": [
        {"ref": "a_up", "days": 0}, {"ref": "a_down", "days": 0}]})
    sig["data_contract"] = {"version": 1, "inputs": {"close": 3}, "ohlc_window": 0}
    cs.validate(sig)  # 不抛异常


def test_composite_contract_matches_equivalent_atom():
    """复合自带契约时，输出须与「等价的单原子(同样契约)」逐行一致。

    这是原子库拆分的核心不变量：把带契约的多条件信号拆成「无契约原子 +
    带契约复合」后，unknown 判定不能漂移（guard 不能随 ref+days 平移加严，
    也不能整体丢失）。等价单原子 = 把复合内联展开回同一组条件。
    """
    from datetime import date as _date
    from app.strategy.strict_signal_data import attach_calendar

    sym = ["X"] * 20
    dates = [_date(2026, 1, i) for i in range(1, 21)]
    closes = [float(i) for i in range(1, 21)]
    df = pl.DataFrame({
        "symbol": sym, "date": dates, "close": closes,
        "open": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
        "amount": closes,
    })
    df = attach_calendar(df, dates)
    contract = {"version": 1, "inputs": {"close": 3, "open": 3, "high": 3, "low": 3, "amount": 3},
                "ohlc_window": 3}

    # 无契约纯积木原子
    a_up = atom("a_up", "close", ">", "0")
    a_pos = atom("a_pos", "amount", ">", "0")
    cat = {"a_up": a_up, "a_pos": a_pos}

    # 复合：ref(a_up,0) AND ref(a_up,8) AND ref(a_pos,0) — 自带契约
    comp = composite("c_ct", {"op": "and", "args": [
        {"ref": "a_up", "days": 0}, {"ref": "a_up", "days": 8}, {"ref": "a_pos", "days": 0}]})
    comp["data_contract"] = contract

    # 等价单原子：把同样的条件内联（含 days=8 偏移）+ 同样契约
    equiv = {
        "id": "a_equiv", "name": "a_equiv", "kind": "entry", "timeframe": "daily",
        "conditions": [
            {"left": "close", "op": ">", "right": "0", "leftDays": 0, "rightDays": 0},
            {"left": "close", "op": ">", "right": "0", "leftDays": 8, "rightDays": 0},
            {"left": "amount", "op": ">", "right": "0", "leftDays": 0, "rightDays": 0},
        ],
        "data_contract": contract,
    }
    exprs = cs.build_expressions([comp, equiv], catalog=cat)
    df = df.with_columns([
        exprs["csg_c_ct"].alias("comp"),
        exprs["csg_a_equiv"].alias("equiv"),
    ])
    assert df["comp"].to_list() == df["equiv"].to_list()


def test_composite_contract_inherits_eps_tolerance_to_inlined_atoms():
    """带契约复合内联无契约原子时，>=/<= 边界比较须继承复合契约的 8-ULP 容差。

    原子库采用「无契约纯积木原子 + 带契约复合」。原单信号带契约时，其 <=/>=
    比较会加 8-ULP 容差以抵消浮点阈值相等误差。拆成无契约原子后若丢失该容差，
    会在边界处与原信号不等价。此测试锁定：复合层把自身契约的 eps 语义下传给
    内联原子，使拆分前后逐行一致。
    """
    # 用自定义因子制造浮点边界：ratio = x/10，当 x=3 时理论值 0.3，浮点可能略大于 0.3
    # 通过 materialize_factor_columns 注入一个表达式列来验证 <= 0.3 的容差行为
    from datetime import date as _date, timedelta
    from app.strategy.strict_signal_data import attach_calendar

    n = 40
    sym = ["X"] * n
    base = _date(2026, 1, 1)
    dates = [base + timedelta(days=i) for i in range(n)]
    # close 取 0.1..4.0，制造 close*0.1 在 0.3 附近的浮点值
    vals = [0.1 * k for k in range(1, n + 1)]
    df = pl.DataFrame({
        "symbol": sym, "date": dates, "close": vals,
        "open": vals, "high": [v + 1 for v in vals], "low": [v - 1 for v in vals],
        "amount": [1.0] * n,
    })
    df = attach_calendar(df, sorted(set(dates)))

    contract = {"version": 1, "inputs": {"close": 3}, "ohlc_window": 0}
    # 无契约原子：close <= 0.3
    a_le = atom("a_le", "close", "<=", "0.3")
    # 带契约复合：and(ref(a_le,0), ref(a_up,0))，a_up 恒真
    comp = composite("c_eps", {"op": "and", "args": [
        {"ref": "a_le", "days": 0}, {"ref": "a_up", "days": 0}]})
    comp["data_contract"] = contract
    # 等价单原子（带同样契约，含两个条件）
    equiv = {
        "id": "a_equiv_eps", "name": "a_equiv_eps", "kind": "entry", "timeframe": "daily",
        "conditions": [
            {"left": "close", "op": "<=", "right": "0.3", "leftDays": 0, "rightDays": 0},
            {"left": "close", "op": ">", "right": "0", "leftDays": 0, "rightDays": 0},
        ],
        "data_contract": contract,
    }
    exprs = cs.build_expressions([comp, equiv], catalog={"a_le": a_le, "a_up": atom("a_up", "close", ">", "0")})
    df = df.with_columns([exprs["csg_c_eps"].alias("comp"), exprs["csg_a_equiv_eps"].alias("equiv")])
    assert df["comp"].to_list() == df["equiv"].to_list()
    # 若无 eps 继承，close=0.30000000000000004 处复合会判 False 而等价原子判 True → 失败
