"""策略种子回归: 历史策略仅补升级用户, GF-000610 七策略面向所有安装。"""

from __future__ import annotations

from pathlib import Path

from app.strategy.strategy_seeds import (
    BASE_T_MARKER, BASE_T_SEED_FILES,
    DIVERGENCE_MARKER, DIVERGENCE_SEED_FILES,
    GF_000610_MARKER, GF_000610_SEED_FILES, SEEDS_DIR, SWING_MARKER, SWING_SEED_FILES,
    YYD_CENTER_MARKER, YYD_CENTER_SEED_FILES, seed_missing_builtins,
)

_EXPECTED_COUNT = len([f for f in SEEDS_DIR.glob("*.py") if not f.name.startswith("_")])


def _make_upgrade_dir(tmp_path) -> None:
    """造升级用户痕迹: 拉过日K (旧版本使用的最普遍特征)。"""
    d = tmp_path / "kline_daily" / "date=2026-01-01"
    d.mkdir(parents=True)
    (d / "part.parquet").write_bytes(b"fake")


def test_fresh_install_seeds_gf_000610_and_skips_legacy_strategies(tmp_path):
    """新装用户获得面向所有安装的 GF-000610、中枢研究和高抛低吸策略。"""
    n = seed_missing_builtins(tmp_path)
    custom = tmp_path / "strategies" / "custom"
    expected = GF_000610_SEED_FILES | YYD_CENTER_SEED_FILES | SWING_SEED_FILES | BASE_T_SEED_FILES | DIVERGENCE_SEED_FILES

    assert n == len(expected) == 17
    assert {p.name for p in custom.glob("*.py")} == expected
    assert (custom / YYD_CENTER_MARKER).exists()
    assert (custom / SWING_MARKER).exists()
    assert (custom / BASE_T_MARKER).exists()
    assert (custom / DIVERGENCE_MARKER).exists()
    assert "skipped" in (custom / ".seeded_v1").read_text(encoding="utf-8")
    assert (custom / GF_000610_MARKER).exists()


def test_fresh_install_does_not_seed_legacy_later_after_usage(tmp_path):
    """新用户用久后重启仍保留七策略, 但不会回头补旧版 25 策略。"""
    seed_missing_builtins(tmp_path)
    _make_upgrade_dir(tmp_path)

    assert seed_missing_builtins(tmp_path) == 0
    custom = tmp_path / "strategies" / "custom"
    assert {p.name for p in custom.glob("*.py")} == (
        GF_000610_SEED_FILES | YYD_CENTER_SEED_FILES | SWING_SEED_FILES | BASE_T_SEED_FILES | DIVERGENCE_SEED_FILES
    )


def test_watchlist_counts_as_upgrade_trace(tmp_path):
    """自选记录也构成升级痕迹 (没拉过K线但用过的用户)。"""
    w = tmp_path / "user_data"
    w.mkdir(parents=True)
    (w / "watchlist.parquet").write_bytes(b"fake")

    assert seed_missing_builtins(tmp_path) == _EXPECTED_COUNT


def test_seed_writes_all_and_marks(tmp_path):
    _make_upgrade_dir(tmp_path)
    n = seed_missing_builtins(tmp_path)

    assert n == _EXPECTED_COUNT
    custom = tmp_path / "strategies" / "custom"
    assert len(list(custom.glob("*.py"))) == _EXPECTED_COUNT
    assert (custom / ".seeded_v1").exists()
    # 幂等: 标记存在, 再次启动零写入
    assert seed_missing_builtins(tmp_path) == 0


def test_seed_never_overwrites_existing(tmp_path):
    custom = tmp_path / "strategies" / "custom"
    custom.mkdir(parents=True)
    # 用户已自行迁移过 1 个 (内容改成自定义版本) + 1 个自己的策略
    # (已有策略文件本身就是升级痕迹)
    (custom / "boll_breakout.py").write_text("# 用户自定义版本\n", encoding="utf-8")
    (custom / "my_own.py").write_text("# 用户自己的策略\n", encoding="utf-8")

    n = seed_missing_builtins(tmp_path)

    assert n == _EXPECTED_COUNT - 1
    assert (custom / "boll_breakout.py").read_text(encoding="utf-8") == "# 用户自定义版本\n"
    assert (custom / "my_own.py").read_text(encoding="utf-8") == "# 用户自己的策略\n"
    assert len(list(custom.glob("*.py"))) == _EXPECTED_COUNT + 1


def test_seed_no_resurrect_after_user_deletes(tmp_path):
    _make_upgrade_dir(tmp_path)
    seed_missing_builtins(tmp_path)
    custom = tmp_path / "strategies" / "custom"
    for f in custom.glob("*.py"):
        f.unlink()

    assert seed_missing_builtins(tmp_path) == 0
    assert len(list(custom.glob("*.py"))) == 0


def test_seeded_strategies_load_in_engine(tmp_path):
    from app.strategy.engine import StrategyEngine

    _make_upgrade_dir(tmp_path)
    seed_missing_builtins(tmp_path)
    engine = StrategyEngine(strategy_dirs=[tmp_path / "strategies" / "custom"])

    assert not engine.load_errors(), engine.load_errors()
    assert len(engine.list_strategies()) == _EXPECTED_COUNT
    assert GF_000610_SEED_FILES <= {
        Path(s.file_path).name for s in engine.strategy_definitions() if s.file_path
    }


def test_existing_legacy_seed_marker_still_receives_gf_000610_once(tmp_path):
    """已完成旧种子迁移的用户也会收到新批次, 不重跑全部旧种子。"""
    custom = tmp_path / "strategies" / "custom"
    custom.mkdir(parents=True)
    (custom / ".seeded_v1").write_text("seeded: 25\n", encoding="utf-8")

    assert seed_missing_builtins(tmp_path) == 17
    assert {p.name for p in custom.glob("*.py")} == (
        GF_000610_SEED_FILES | YYD_CENTER_SEED_FILES | SWING_SEED_FILES | BASE_T_SEED_FILES | DIVERGENCE_SEED_FILES
    )
    assert (custom / YYD_CENTER_MARKER).exists()
    assert (custom / SWING_MARKER).exists()
    assert (custom / BASE_T_MARKER).exists()
    assert (custom / DIVERGENCE_MARKER).exists()
    assert seed_missing_builtins(tmp_path) == 0


def test_gf_000610_seed_preserves_existing_user_file_and_does_not_resurrect(tmp_path):
    custom = tmp_path / "strategies" / "custom"
    custom.mkdir(parents=True)
    filename = "single_yang_holds.py"
    user_code = "# 用户已修改的版本\n"
    (custom / filename).write_text(user_code, encoding="utf-8")

    assert seed_missing_builtins(tmp_path) == _EXPECTED_COUNT - 1
    assert (custom / filename).read_text(encoding="utf-8") == user_code

    for path in custom.glob("*.py"):
        path.unlink()
    assert seed_missing_builtins(tmp_path) == 0
    assert len(list(custom.glob("*.py"))) == 0
