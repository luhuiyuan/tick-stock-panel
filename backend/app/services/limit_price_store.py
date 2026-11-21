"""涨停价历史小表 —— 按 (symbol, trade_date) 存储规则推断涨跌停价。

设计动机
--------------------------------------------------------------------------
覆盖榜 price_facts 原先只用"规则推算"(前收 × 板块比例)判定 strict_close_limit。
规则推算依赖 MARKET_INDEX.diff()==1(相邻交易日连续), 在长假/行情缺日会断链留 null。

现规则已完善: 除权除息日改用 adj_factor 权息四元组算高精度修正昨收(见 xdxr_ref),
对齐 eltdx 官方口径; 同时保留我们更正确的历史时点化涨跌幅比例。涨停价不再依赖
任何外部接口, 全部由本地规则计算并落盘到本表。

存储范式照搬 ``data/adj_factor/all.parquet``: 单文件 (symbol, trade_date) 主键,
乐观快照 + 原子发布(optimistic_partition_write), 追加只增不改历史。
"""

from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from app.services.fs_utils import optimistic_partition_write

logger = logging.getLogger(__name__)

# 相对 data_dir 的存储路径
REL_PATH = "limit_prices/all.parquet"

# 主键 + 两个规则推断价字段。
KEY = ["symbol", "trade_date"]
PRICE_COLS = [
    "limit_up_inferred",
    "limit_down_inferred",
]
COLUMNS = KEY + PRICE_COLS

_SCHEMA = {
    "symbol": pl.String,
    "trade_date": pl.Date,
    "limit_up_inferred": pl.Float64,
    "limit_down_inferred": pl.Float64,
}


def _empty() -> pl.DataFrame:
    return pl.DataFrame(schema=_SCHEMA)


def path_for(data_dir: Path) -> Path:
    return Path(data_dir) / REL_PATH


def load(data_dir: Path) -> pl.DataFrame:
    """读取整张小表; 文件缺失时返回带 schema 的空帧。"""
    p = path_for(data_dir)
    if not p.exists():
        return _empty()
    try:
        df = pl.read_parquet(p)
    except Exception as e:  # 损坏文件不阻断覆盖榜, 降级为空(下次回填重建)
        logger.warning("limit_prices 读取失败, 降级为空: %s", e)
        return _empty()
    # 对齐列/类型, 容忍旧版本缺列; 旧版 api 列自动丢弃。
    return _normalize(df).select(COLUMNS).unique(subset=KEY, keep="last")


def _normalize(new_data: pl.DataFrame) -> pl.DataFrame:
    """把任意输入对齐到 canonical schema(缺失列补 null, 去重复键)。"""
    n = new_data.height
    out = pl.DataFrame(
        {
            col: (
                new_data[col].cast(_SCHEMA[col], strict=False)
                if col in new_data.columns
                else pl.Series([None] * n, dtype=_SCHEMA[col])
            )
            for col in COLUMNS
        }
    )
    if out.height == 0:
        return out
    return out.filter(pl.col("symbol").is_not_null() & pl.col("trade_date").is_not_null())


def _coalesce_merge(existing: pl.DataFrame, new_data: pl.DataFrame) -> pl.DataFrame:
    """按 (symbol, trade_date) 合并; 每个价字段用"新值优先、null 用旧值"。

    重算推断价时, 新值为 null 不能抹掉旧的非 null 值。同批内重复键先 keep last,
    再与 existing 按列 coalesce(新优先)。
    """
    new_dedup = new_data.unique(subset=KEY, keep="last")
    if existing.is_empty():
        return new_dedup.sort(KEY)
    merged = new_dedup.join(
        existing, on=KEY, how="full", coalesce=True, suffix="_old"
    )
    exprs = []
    for c in PRICE_COLS:
        exprs.append(pl.coalesce(pl.col(c), pl.col(f"{c}_old")).alias(c))
    return merged.select([pl.col(k) for k in KEY] + exprs).sort(KEY)


def merge(
    data_dir: Path,
    new_data: pl.DataFrame,
    *,
    write_lock=None,
) -> tuple[int, pl.DataFrame]:
    """追加/更新推断价, 返回 (新增键数, 合并后的整表)。"""
    new_data = _normalize(new_data)
    if new_data.is_empty():
        return 0, load(data_dir)
    out = path_for(data_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    added = 0
    table: dict[str, pl.DataFrame] = {}

    def build(existing: pl.DataFrame) -> pl.DataFrame:
        nonlocal added, table
        existing = _normalize(existing) if existing.height else _empty()
        before_keys = set(map(tuple, existing.select(KEY).iter_rows()))
        merged = _coalesce_merge(existing, new_data)
        after_keys = set(map(tuple, merged.select(KEY).iter_rows()))
        added = len(after_keys - before_keys)
        table["merged"] = merged
        return merged

    result = optimistic_partition_write(out, build, write_lock)
    return added, (table.get("merged") if "merged" in table else result)


def store_inferred(
    data_dir: Path,
    inferred: pl.DataFrame,
    *,
    write_lock=None,
) -> tuple[int, pl.DataFrame]:
    """落盘规则推断价(limit_up_inferred/limit_down_inferred)。

    ``inferred`` 需含 columns: symbol, trade_date, limit_up_inferred, limit_down_inferred。
    """
    if inferred is None or inferred.is_empty():
        return 0, load(data_dir)
    return merge(data_dir, inferred, write_lock=write_lock)
