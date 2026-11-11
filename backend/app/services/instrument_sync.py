"""标的维表同步服务。

盘前 9:10 调用 tf.exchanges.get_instruments("SH"/"SZ"/"BJ", type="stock")
获取全量标的元数据，flatten ext 字段，写入 instruments.parquet。

Starter+ 盘后可用 quotes.get(universes) 顺便补充 name。
"""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

from app.market_time import cn_today
from app.services.fs_utils import atomic_write_parquet
from app.tickflow import client as tf_client
from app.tickflow.client import get_client

logger = logging.getLogger(__name__)

_EXCHANGES = ["SH", "SZ", "BJ"]


def _flatten_instruments(items: list[dict], *, current_limits: bool = True) -> list[dict]:
    """把 SDK 返回的 Instrument 列表 flatten 成扁平行。"""
    rows = []
    for item in items:
        row = {
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "code": item.get("code"),
            "exchange": item.get("exchange"),
            "region": item.get("region"),
            "type": item.get("type"),
        }
        ext = item.get("ext") or {}
        row["listing_date"] = ext.get("listing_date", item.get("listing_date"))
        row["total_shares"] = ext.get("total_shares", item.get("total_shares"))
        row["float_shares"] = ext.get("float_shares", item.get("float_shares"))
        row["tick_size"] = ext.get("tick_size", item.get("tick_size"))
        row["limit_up"] = ext.get("limit_up", item.get("limit_up")) if current_limits else None
        row["limit_down"] = ext.get("limit_down", item.get("limit_down")) if current_limits else None
        rows.append(row)
    return rows


def _fetch_instruments_via_provider() -> list[dict] | None:
    """若当前日K数据源不是 tickflow 且该 provider 提供 get_instruments, 用它拉标的维表。

    返回 flatten 行列表; 未命中(仍应走 tickflow)时返回 None。
    标的维表跟随日K数据源(二者天然耦合, 无独立偏好项)。
    """
    from app.services import preferences

    provider_name = preferences.get_daily_data_provider()
    if provider_name == "tickflow":
        return None
    from app.data_providers import custom as custom_sources
    from app.data_providers.registry import _PROVIDERS as _builtin_providers

    if provider_name not in _builtin_providers and not custom_sources.is_custom_provider(provider_name):
        return None
    provider = (_builtin_providers[provider_name]() if provider_name in _builtin_providers
                else custom_sources.get_provider(provider_name))
    if not hasattr(provider, "get_instruments"):
        return None
    try:
        items = provider.get_instruments("stock")
        if isinstance(items, pl.DataFrame):
            items = items.to_dicts()
        items = items or []
    except Exception as e:  # noqa: BLE001
        logger.warning("provider %s get_instruments 失败: %s", provider_name, e)
        return None
    rows = _flatten_instruments(items)
    if not rows:
        # 返回 [] 而非 None 会被上层当成"维表同步成功但 0 行", 直接跳过 tickflow
        # 兜底并静默产出空池。这里显式降级, 让上层走 tickflow 拿一份可用维表。
        logger.warning(
            "provider %s 未返回任何标的, 回退 tickflow 维表", provider_name,
        )
        return None
    logger.info("instruments via %s: %d stocks", provider_name, len(rows))
    rows = _enrich_shares_via_provider(provider, rows)
    return rows


def _current_provider_name() -> str:
    """当前日K数据源名, 仅用于日志; 读取失败不影响同步。"""
    try:
        from app.services import preferences

        return preferences.get_daily_data_provider()
    except Exception:  # noqa: BLE001
        return "unknown"


def _enrich_shares_via_provider(provider, rows: list[dict]) -> list[dict]:
    """用 provider 的权威股本接口补全 get_instruments 未提供的股本字段。

    qatdx 的 get_instruments 只返回标识字段, 真实股本在 get_financials("shares")
    里(由 client.finance 返回 zongguben/liutongguben, 已换算成股)。维表若只靠
    历史维表补全, 而历史维表本身也为空, 会陷入死循环永远 null。这里直接从权威
    源补, 才是治本。

    只在 provider 暴露 get_financials 时进行; 单只失败/超时跳过, 不伪造股本。
    """
    if not hasattr(provider, "get_financials"):
        return rows
    need = [r["symbol"] for r in rows if r.get("total_shares") is None]
    if not need:
        return rows

    by_symbol: dict[str, tuple[float | None, float | None]] = {}
    batch_size = 300
    n_batches = (len(need) + batch_size - 1) // batch_size
    failed_batches = 0
    for start in range(0, len(need), batch_size):
        batch = need[start:start + batch_size]
        try:
            part = provider.get_financials("shares", batch)
        except Exception as exc:  # noqa: BLE001
            failed_batches += 1
            logger.warning(
                "provider %s get_financials(shares) 批次 %d/%d 失败, 该批保持 null: %s",
                getattr(provider, "name", "?"), start // batch_size + 1, n_batches, exc,
            )
            continue
        if part is None or part.is_empty():
            continue
        for s in part.to_dicts():
            by_symbol[s["symbol"]] = (s.get("total_shares"), s.get("float_shares"))
    if not by_symbol:
        if failed_batches:
            logger.warning(
                "provider %s get_financials(shares) 全部 %d 批失败, 维表股本保持 null",
                getattr(provider, "name", "?"), failed_batches,
            )
        return rows

    filled = 0
    for r in rows:
        pair = by_symbol.get(r["symbol"])
        if not pair:
            continue
        tot, flt = pair
        if r.get("total_shares") is None:
            r["total_shares"] = tot if tot is not None else flt
        if r.get("float_shares") is None and flt is not None:
            r["float_shares"] = flt
        if r.get("total_shares") is not None or r.get("float_shares") is not None:
            filled += 1
    logger.info(
        "instruments 股本经 %s.get_financials 权威补全: %d/%d 标的",
        getattr(provider, "name", "?"), filled, len(need),
    )
    return rows


def sync_instruments(data_dir: Path) -> int:
    """全量同步标的维表 → data/instruments/instruments.parquet。

    返回写入的行数。
    """
    all_rows = _fetch_instruments_via_provider()
    if all_rows is None:
        # 未命中非 tickflow provider → 走 tickflow 直连
        tf = get_client()
        # 免费接口的限价来自历史行情，不能盖上同步日期当作当日权威价。
        current_limits = tf_client.current_mode() == "api_key"
        all_rows = []
        for ex in _EXCHANGES:
            try:
                items = tf.exchanges.get_instruments(ex, instrument_type="stock")
                if items:
                    all_rows.extend(_flatten_instruments(items, current_limits=current_limits))
                    logger.info("instruments %s: %d stocks", ex, len(items))
            except Exception as e:
                logger.warning("get_instruments(%s) failed: %s", ex, e)

    if not all_rows:
        # 不写空表(避免把已有维表覆盖掉), 但必须留下明确原因: 0 行会让下游标的池
        # 直接变空, 日K/分钟/策略全部无事可做, 只在进度条上显示"0 只标的"。
        logger.error(
            "instruments 同步失败: 所有数据源均未返回标的 (当前 provider=%s), "
            "标的池将为空。请检查数据源可用性或改用其他 provider",
            _current_provider_name(),
        )
        return 0

    df = pl.DataFrame(all_rows)
    df = _preserve_share_fields(df, data_dir)
    df = df.with_columns(pl.lit(cn_today()).alias("as_of"))

    out = data_dir / "instruments" / "instruments.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(df, out)

    logger.info("instruments synced: %d rows → %s", df.height, out)
    return df.height


# 股本与静态交易规则字段: 部分行情源的标的维表只有代码/名称/市场/类型。
# 这些字段缺失会让 enriched 的 turnover_rate / 市值为空, 进而使
# 依赖换手率与流通市值的策略筛不出任何标的 —— 因此绝不能用 null 覆盖已有值。
# 涨跌停价随交易日变化，不从历史维表继承。
_SHARE_FIELDS = (
    "listing_date", "total_shares", "float_shares", "tick_size",
)


def _preserve_share_fields(df: pl.DataFrame, data_dir: Path) -> pl.DataFrame:
    """用现有维表补全 provider 未提供的股本/交易规则字段 (按 symbol 左连接)。

    新增标的仍保留 null (没有历史值可用, 不伪造); 只在同一 symbol 上把
    provider 的空值替换为既有值, 并在日志里给出补全规模。
    """
    available = [c for c in _SHARE_FIELDS if c in df.columns]
    if not available:
        return df
    if all(df[c].null_count() == 0 for c in available):
        return df  # provider 已提供全部字段, 无需补全

    path = data_dir / "instruments" / "instruments.parquet"
    if not path.exists():
        logger.warning(
            "instruments provider 未提供股本字段且无历史维表可补全: "
            "enriched 换手率/市值将为 null, 依赖它们的策略会筛不出标的"
        )
        return df
    try:
        existing = pl.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("instruments 历史维表读取失败, 无法补全股本字段: %s", e)
        return df
    if "symbol" not in existing.columns:
        return df

    keep = ["symbol", *[c for c in available if c in existing.columns]]
    if len(keep) <= 1:
        return df
    merged = df.join(existing.select(keep), on="symbol", how="left", suffix="_prev")
    filled: list[str] = []
    for col in keep[1:]:
        prev = f"{col}_prev"
        if prev not in merged.columns:
            continue
        # 逐行 coalesce: provider 有值用 provider, 为空才回落历史值。这样
        # 「部分标的缺字段」也能补齐, 而不只处理整列为空的源。
        if merged[col].null_count() and merged[prev].is_not_null().any():
            merged = merged.with_columns(
                pl.coalesce(pl.col(col), pl.col(prev)).alias(col)
            )
            filled.append(col)
    merged = merged.drop([f"{c}_prev" for c in keep[1:] if f"{c}_prev" in merged.columns])
    if filled:
        restored = int(merged.select(
            [pl.col(c).is_not_null().sum() for c in filled]
        ).row(0)[0])
        logger.info(
            "instruments provider 缺 %s, 已逐行从历史维表补全 (首列非空 %d/%d)",
            "/".join(filled), restored, merged.height,
        )
    return merged


def enrich_names_from_quotes(
    data_dir: Path,
    quotes_data: list[dict],
) -> int:
    """从 quotes 响应中提取 name，更新 instruments 维表（兜底补充）。

    盘后 quotes.get(universes) 返回的数据中包含 ext.name，
    用来补充 instruments 中可能缺失的 name。
    """
    if not quotes_data:
        return 0

    # 构建 symbol → name 映射
    name_map: dict[str, str] = {}
    for q in quotes_data:
        symbol = q.get("symbol", "")
        ext = q.get("ext") or {}
        name = ext.get("name") or q.get("name", "")
        if symbol and name:
            name_map[symbol] = name

    if not name_map:
        return 0

    inst_path = data_dir / "instruments" / "instruments.parquet"
    if not inst_path.exists():
        return 0

    df = pl.read_parquet(inst_path)

    # 只更新空 name 的行
    updates = pl.DataFrame({
        "symbol": list(name_map.keys()),
        "_new_name": list(name_map.values()),
    })
    df = df.join(updates, on="symbol", how="left")
    df = df.with_columns(
        pl.when(pl.col("name").is_null() | (pl.col("name") == ""))
        .then(pl.col("_new_name"))
        .otherwise(pl.col("name"))
        .alias("name"),
    ).drop("_new_name")

    atomic_write_parquet(df, inst_path)
    logger.info("instruments name enriched from quotes: %d names", len(name_map))
    return len(name_map)
