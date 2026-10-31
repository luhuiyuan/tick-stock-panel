"""随应用分发的策略种子。

旧版 25 个策略只补给有升级痕迹的用户, 保持既有去内置化兼容行为。
GF-000610 的七个新策略则面向所有安装分发: 新装与升级用户都补齐一次。
YYD-CENTER 的中枢研究策略同样面向所有安装补齐一次, 使用独立标记。
所有种入均按文件名幂等, 已存在的用户文件不覆盖, 删除后不复活。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SEEDS_DIR = Path(__file__).resolve().parent / "seeds"
GF_000610_SEED_FILES = frozenset({
    "single_yang_holds.py",
    "dapandibufantan.py",
    "strong_breakout_consolidation.py",
    "surge_accel_pullback.py",
    "surge_bias_pullback.py",
    "crash_day_dip.py",
    "volume_peak_pullback.py",
})
GF_000610_MARKER = ".seeded_gf_000610_a"
YYD_CENTER_SEED_FILES = frozenset({"chan_center_range.py"})
YYD_CENTER_MARKER = ".seeded_yyd_center"
SWING_SEED_FILES = frozenset({
    "grid_trading.py",
    "turtle_channel.py",
    "boll_rsi_revert.py",
    "n_limit_pullback.py",
    "first_limit_up.py",
})
SWING_MARKER = ".seeded_swing_batch"
BASE_T_SEED_FILES = frozenset({
    "intraday_base_t_macd.py",
    "intraday_base_t_band.py",
})
BASE_T_MARKER = ".seeded_base_t"
DIVERGENCE_SEED_FILES = frozenset({
    "macd_triple_bottom.py",
    "macd_triple_top.py",
})
DIVERGENCE_MARKER = ".seeded_macd_triple"
OVERNIGHT_TAIL_SEED_FILES = frozenset({"overnight_tail_hold.py"})
OVERNIGHT_TAIL_MARKER = ".seeded_overnight_tail"
CUSTOM_POOL_SEED_FILES = frozenset({
    "custom_consecutive_limit_ups_v2.py",
    "custom_despair_reversal_leader.py",
    "custom_two_board_special.py",
    "custom_ma75_kdj_oversold.py",
})
CUSTOM_POOL_MARKER = ".seeded_custom_pool"
# 情绪择时示例 (原 examples/strategies/custom_volume_shrink_pullback.py) 转为内置种子。
# 注意: `_seed_once` 以标记文件保证幂等, 标记一旦写下就整批跳过, 所以向既有批次
# 追加策略时必须另起标记, 否则已写过旧标记的用户永远收不到新增项。
CUSTOM_POOL_V2_SEED_FILES = frozenset({"custom_volume_shrink_pullback.py"})
CUSTOM_POOL_V2_MARKER = ".seeded_custom_pool_v2"


def _has_upgrade_traces(data_dir: Path) -> bool:
    """数据目录是否有旧版本的使用痕迹 (新装首次启动为 False)。

    三个信号任一命中即视为升级用户:
    - strategies/{custom,ai,composite} 下已有任何策略文件 (用户自己的)
    - kline_daily 已有 parquet (拉过日K; 内置策略跑起来必须有数据)
    - watchlist.parquet 存在 (加过自选)
    """
    for sub in ("custom", "ai", "composite"):
        if any((data_dir / "strategies" / sub).glob("*.py")):
            return True
    if any((data_dir / "kline_daily").rglob("*.parquet")):
        return True
    return (data_dir / "user_data" / "watchlist.parquet").exists()


def _copy_missing_seeds(data_dir: Path, seed_files: set[str] | frozenset[str]) -> int:
    """安全校验后把指定版本控制种子复制到 custom, 已有同名文件绝不覆盖。"""
    from app.strategy.ai_generator import AIStrategyGenerator

    custom_dir = data_dir / "strategies" / "custom"
    custom_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for filename in sorted(seed_files):
        seed = SEEDS_DIR / filename
        if not seed.is_file():
            logger.warning("strategy seed %s missing from package", filename)
            continue
        target = custom_dir / filename
        if target.exists():
            continue
        code = seed.read_text(encoding="utf-8")
        try:
            AIStrategyGenerator._validate_safety(code)
        except (ValueError, SyntaxError) as e:
            logger.warning("strategy seed %s skipped (validation): %s", filename, e)
            continue
        target.write_text(code, encoding="utf-8")
        written += 1
    return written


def _seed_once(data_dir: Path, filenames: frozenset[str], marker_name: str, label: str) -> int:
    """所有安装类型均补入一批策略一次; 已有同名文件不覆盖, 标记后删除不复活。"""
    custom_dir = data_dir / "strategies" / "custom"
    custom_dir.mkdir(parents=True, exist_ok=True)
    marker = custom_dir / marker_name
    if marker.exists():
        return 0
    written = _copy_missing_seeds(data_dir, filenames)
    marker.write_text(f"seeded: {written}\n", encoding="utf-8")
    if written:
        logger.info("strategy seeds: wrote %d %s strategies", written, label)
    return written


def _seed_gf_000610(data_dir: Path) -> int:
    """所有安装类型均补入 GF-000610 七策略一次; 不受旧版种子标记影响。"""
    return _seed_once(data_dir, GF_000610_SEED_FILES, GF_000610_MARKER, "GF-000610")


def _seed_yyd_center(data_dir: Path) -> int:
    """所有安装类型均补入 Chan 中枢研究策略一次。"""
    return _seed_once(data_dir, YYD_CENTER_SEED_FILES, YYD_CENTER_MARKER, "YYD-CENTER")


def _seed_swing(data_dir: Path) -> int:
    """所有安装类型均补入高抛低吸批次一次。"""
    return _seed_once(data_dir, SWING_SEED_FILES, SWING_MARKER, "SWING")


def _seed_base_t(data_dir: Path) -> int:
    """所有安装类型均补入底仓做T一次。已有同名文件不覆盖，删除后不复活。"""
    return _seed_once(data_dir, BASE_T_SEED_FILES, BASE_T_MARKER, "BASE-T")


def _seed_divergence(data_dir: Path) -> int:
    """所有安装类型均补入日线三重背离选股一次。已有同名文件不覆盖，删除后不复活。"""
    return _seed_once(data_dir, DIVERGENCE_SEED_FILES, DIVERGENCE_MARKER, "DIVERGENCE")


def _seed_overnight_tail(data_dir: Path) -> int:
    """所有安装类型均补入尾盘持股一次。已有同名文件不覆盖，删除后不复活。"""
    return _seed_once(data_dir, OVERNIGHT_TAIL_SEED_FILES, OVERNIGHT_TAIL_MARKER, "OVERNIGHT-TAIL")


def _seed_custom_pool(data_dir: Path) -> int:
    """所有安装类型均补入本机自定义策略池一次。已有同名文件不覆盖，删除后不复活。"""
    return _seed_once(data_dir, CUSTOM_POOL_SEED_FILES, CUSTOM_POOL_MARKER, "CUSTOM-POOL")


def _seed_custom_pool_v2(data_dir: Path) -> int:
    """所有安装类型均补入放量缩量回踩一次。已有同名文件不覆盖，删除后不复活。"""
    return _seed_once(data_dir, CUSTOM_POOL_V2_SEED_FILES, CUSTOM_POOL_V2_MARKER, "CUSTOM-POOL-V2")


def seed_missing_builtins(data_dir: Path) -> int:
    """补入既有升级策略和面向所有安装的 GF-000610 策略种子。

    - `.seeded_v1` 保持原语义: 25 个旧策略仅补升级用户一次, 新装跳过。
    - GF-000610 使用独立标记: 新装与升级用户均补齐七个新增策略一次。
    - YYD-CENTER 使用独立标记: 新装与升级用户均补齐中枢研究策略一次。
    - SWING 使用独立标记: 新装与升级用户均补齐网格、海龟、布林 RSI、N 字和首板一次。
    - BASE-T 使用独立标记: 新装与升级用户均补齐 MACD 底仓回转和等量对冲一次。
    - DIVERGENCE 使用独立标记: 新装与升级用户均补齐日线三重顶/底背离选股一次。
    - OVERNIGHT-TAIL 使用独立标记: 新装与升级用户均补齐尾盘持股一次。
    - CUSTOM-POOL 使用独立标记: 新装与升级用户均补齐连板改进版、绝望弱转强、2连板特别版和 MA75+KDJ 超卖一次。
    - CUSTOM-POOL-V2 使用独立标记: 新装与升级用户均补齐放量缩量回踩一次(原为 emotion-timing 示例)。
    - 任意种子都只写缺失文件, 不覆盖用户同名文件; 标记保证删除后不复活。
    - 写入前通过策略安全校验; 单个种子异常不会阻断应用启动。
    """
    custom_dir = data_dir / "strategies" / "custom"
    custom_dir.mkdir(parents=True, exist_ok=True)
    marker = custom_dir / ".seeded_v1"
    written = 0
    if not marker.exists():
        if not _has_upgrade_traces(data_dir):
            marker.write_text("skipped: fresh install\n", encoding="utf-8")
            logger.info("strategy seeds: fresh install, skip legacy seeds")
        else:
            legacy_seeds = {
                seed.name for seed in SEEDS_DIR.glob("*.py")
                if not seed.name.startswith("_")
                and seed.name not in GF_000610_SEED_FILES
                and seed.name not in YYD_CENTER_SEED_FILES
                and seed.name not in SWING_SEED_FILES
                and seed.name not in BASE_T_SEED_FILES
                and seed.name not in DIVERGENCE_SEED_FILES
                and seed.name not in OVERNIGHT_TAIL_SEED_FILES
                and seed.name not in CUSTOM_POOL_SEED_FILES
                and seed.name not in CUSTOM_POOL_V2_SEED_FILES
            }
            written += _copy_missing_seeds(data_dir, legacy_seeds)
            marker.write_text(f"seeded: {written}\n", encoding="utf-8")
            if written:
                logger.info("strategy seeds: wrote %d legacy strategies", written)

    written += _seed_gf_000610(data_dir)
    written += _seed_yyd_center(data_dir)
    written += _seed_swing(data_dir)
    written += _seed_base_t(data_dir)
    written += _seed_divergence(data_dir)
    written += _seed_overnight_tail(data_dir)
    written += _seed_custom_pool(data_dir)
    written += _seed_custom_pool_v2(data_dir)
    return written
