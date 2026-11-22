"""覆盖榜对复合信号的支持: catalog 加载、依赖展开、指纹随原子失效。"""
import json
from pathlib import Path

import polars as pl


def _write_sig(root: Path, sig: dict):
    d = root / "user_data/custom_signals"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sig['id']}.json").write_text(json.dumps(sig))


def _atom(sid, right="0"):
    return {"id": sid, "name": sid, "kind": "entry", "timeframe": "daily",
            "conditions": [{"left": "close", "op": ">", "right": right,
                            "leftDays": 0, "rightDays": 0}]}


def _composite(sid, expr):
    return {"id": sid, "name": sid, "kind": "entry", "timeframe": "daily",
            "type": "composite", "expr": expr}


def test_inputs_loads_signal_catalog(tmp_path):
    from app.services.coverage_replay import Inputs
    import hashlib, json as _json

    _write_sig(tmp_path, _atom("a1"))
    _write_sig(tmp_path, _composite("c1", {"op": "and", "args": [
        {"ref": "a1", "days": 0}, {"ref": "a1", "days": 1}]}))

    manifest = {}
    inp = Inputs(manifest, tmp_path, {})
    assert "a1" in inp.signals and "c1" in inp.signals
    assert inp._atom_refs(inp.signals["c1"]) == {"a1"}


def test_dependencies_expand_referenced_atom_fields(tmp_path):
    from app.services.coverage_replay import Inputs

    atom = _atom("a1")
    atom["data_contract"] = {"version": 1, "inputs": {"amount": 5}, "ohlc_window": 0}
    atom["conditions"] = [{"left": "amount", "op": ">", "right": "0",
                           "leftDays": 0, "rightDays": 0}]
    _write_sig(tmp_path, atom)
    comp = _composite("c1", {"op": "or", "args": [
        {"ref": "a1", "days": 0}, {"ref": "a1", "days": 2}]})
    _write_sig(tmp_path, comp)

    inp = Inputs({}, tmp_path, {})
    deps = inp.dependencies(comp)
    # 引用原子的字段 amount 应进入依赖
    assert "amount" in deps
    # 原子 id 本身也会因 ref 名字被 regex 扫到, 无妨
    assert "a1" in deps


def test_scoped_fingerprint_changes_when_atom_changes(tmp_path):
    from app.services.coverage_replay import Inputs

    atom = _atom("a1", right="0")
    comp = _composite("c1", {"ref": "a1", "days": 0})
    _write_sig(tmp_path, atom)
    _write_sig(tmp_path, comp)

    f1 = Inputs({}, tmp_path, {}).scoped(comp)
    # 修改被引用原子的阈值 → 复合指纹必须变化
    _write_sig(tmp_path, _atom("a1", right="999"))
    f2 = Inputs({}, tmp_path, {}).scoped(comp)
    assert f1 != f2


def test_scoped_ignores_unrelated_atom_change(tmp_path):
    from app.services.coverage_replay import Inputs

    _write_sig(tmp_path, _atom("a1"))
    _write_sig(tmp_path, _atom("other"))
    comp = _composite("c1", {"ref": "a1", "days": 0})
    _write_sig(tmp_path, comp)

    f1 = Inputs({}, tmp_path, {}).scoped(comp)
    _write_sig(tmp_path, _atom("other", right="999"))  # 改的是没被引用的原子
    f2 = Inputs({}, tmp_path, {}).scoped(comp)
    assert f1 == f2
