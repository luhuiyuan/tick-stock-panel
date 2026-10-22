"""QATdx 旁路数据落盘。

只写 data/qatdx/，不进入股票日 K、分钟 K 或财务三大报表目录。
分笔和债券日 K 不在盘后管道里自动全市场拉取，避免打满磁盘和行情主站。
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import polars as pl

from app.services.fs_utils import atomic_write_parquet

logger = logging.getLogger(__name__)


def _provider():
    from app.data_providers import custom as custom_sources
    try:
        return custom_sources.get_provider("qatdx")
    except Exception:  # noqa: BLE001
        return None


def sync_reference_if_selected(data_dir: Path, trade_day: date | None = None) -> None:
    """板块、行业、融资融券、债券列表。对应偏好不是 qatdx，或插件未就绪时跳过。"""
    from app.services import preferences

    day = trade_day or date.today()
    root = data_dir / "qatdx"
    selected = (
        preferences.get_block_data_provider() == "qatdx",
        preferences.get_industry_data_provider() == "qatdx",
        preferences.get_margin_data_provider() == "qatdx",
        preferences.get_bond_data_provider() == "qatdx",
    )
    if not any(selected):
        return
    provider = _provider()
    if provider is None:
        logger.info("qatdx 插件未就绪，跳过旁路参考数据")
        return
    block_on, industry_on, margin_on, bond_on = selected
    if block_on:
        _write(root / "block.parquet", provider.get_blocks())
    if industry_on:
        _write(root / "industry.parquet", provider.get_industries())
    if margin_on:
        _write(root / "margin" / f"{day.isoformat()}.parquet", provider.get_margin(day))
    if bond_on:
        rows = provider.get_instruments("bond")
        frame = pl.DataFrame(rows) if rows else pl.DataFrame()
        _write(root / "bond" / "instruments.parquet", frame)


def sync_transactions(data_dir: Path, symbols: list[str], trade_day: date) -> int:
    provider = _provider()
    if provider is None:
        logger.info("qatdx 插件未就绪，跳过分笔")
        return 0
    frame = provider.get_transactions(symbols, trade_day)
    path = data_dir / "qatdx" / "transaction" / f"{trade_day.isoformat()}.parquet"
    _write(path, frame)
    return frame.height


def sync_bond_daily(data_dir: Path, symbols: list[str], start, end) -> int:
    provider = _provider()
    if provider is None:
        logger.info("qatdx 插件未就绪，跳过债券日K")
        return 0
    frame = provider.get_daily(symbols, start, end, asset_type="bond")
    path = data_dir / "qatdx" / "bond" / "daily.parquet"
    _write(path, frame)
    return frame.height


def _write(path: Path, frame: pl.DataFrame) -> None:
    if frame.is_empty():
        logger.info("qatdx 旁路空数据，不写 %s", path)
        return
    atomic_write_parquet(frame, path)
    logger.info("qatdx 旁路写入 %s (%d 行)", path, frame.height)
