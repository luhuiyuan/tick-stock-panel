"""竞价路径原始采集旁路。

只保存 09:16:15--09:25:15 的五档原始快照和实时累计量/额，供后续
F3/F6/F7 前瞻研究使用。本服务不写 enriched、auction 或 depth5 sealed 数据。
"""
from __future__ import annotations

import logging
import threading
from datetime import date
from pathlib import Path

import polars as pl

from app.market_time import cn_now, cn_today
from app.services.fs_utils import atomic_write_parquet
from app.tickflow.capabilities import Cap

logger = logging.getLogger(__name__)

MAX_SYMBOLS = 200
AUCTION_WINDOW_START = "09:16:15"
AUCTION_WINDOW_END = "09:25:15"
TREND_MCAP_MIN = 20e9
TREND_FLOAT_MIN = 10e9
PATH_COLUMNS = [
    "date", "ts", "symbol", "src_timestamp", "quote_cum_vol", "quote_cum_amount",
    "bid_p1", "bid_v1", "ask_p1", "ask_v1",
    "bid_p2", "bid_v2", "ask_p2", "ask_v2",
    "bid_p3", "bid_v3", "ask_p3", "ask_v3",
    "bid_p4", "bid_v4", "ask_p4", "ask_v4",
    "bid_p5", "bid_v5", "ask_p5", "ask_v5",
]


def _path(data_dir: Path, day: date) -> Path:
    return data_dir / "user_data" / "auction_path" / f"date={day.isoformat()}" / "part.parquet"


class AuctionPathService:
    """集合竞价路径采集器，单例注入 repo 和 app.state。"""

    def __init__(self) -> None:
        self._repo = None
        self._app_state = None
        self._lock = threading.Lock()
        self._day: date | None = None
        self._day_rows: pl.DataFrame = pl.DataFrame()
        self._symbols_cache: tuple[date, list[str]] | None = None
        self._day_load_ok = True

    def set_repo(self, repo) -> None:
        self._repo = repo

    def set_app_state(self, app_state) -> None:
        self._app_state = app_state

    def _trend_symbols(self, today: date) -> list[str]:
        if self._symbols_cache and self._symbols_cache[0] == today:
            return self._symbols_cache[1]
        if not self._repo:
            return []

        data_dir = Path(self._repo.store.data_dir)
        base = data_dir / "kline_daily_enriched"
        # 只使用 T-1 及更早的完整日线，避免盘中/未来分区污染候选池。
        cutoff = f"date={today.isoformat()}"
        parts = sorted(part for part in base.glob("date=*") if part.name < cutoff)[-90:]
        if not parts:
            self._symbols_cache = (today, [])
            return []
        try:
            from app.indicators.pipeline import compute_indicators

            panel = pl.concat(
                [pl.scan_parquet(str(part / "*.parquet")) for part in parts],
                how="diagonal_relaxed",
            ).collect()
            panel = compute_indicators(
                panel,
                needed={"ma5", "ma10", "ma20", "momentum_5d", "momentum_10d", "high_60d", "vol_ratio_5d"},
            )
            last_date = panel["date"].max()
            snap = panel.filter(pl.col("date") == last_date)
            inst = self._repo.get_instruments()
            join_cols = [c for c in ("symbol", "name", "total_shares", "float_shares") if c in inst.columns]
            required = {"symbol", "name", "total_shares", "float_shares"}
            if not required.issubset(join_cols):
                logger.warning("竞价路径采集跳过: instruments 缺少候选池所需股本字段")
                self._symbols_cache = (today, [])
                return []
            snap = snap.join(inst.select(join_cols).unique(subset=["symbol"]), on="symbol", how="left")
            cond = (
                (pl.col("close") >= 3) & (pl.col("close") <= 300)
                & (pl.col("amount") >= 0.8e8)
                & (pl.col("close") * pl.col("total_shares") >= TREND_MCAP_MIN)
                & (pl.col("close") * pl.col("float_shares") >= TREND_FLOAT_MIN)
                & (pl.col("ma5") > pl.col("ma10")) & (pl.col("ma10") > pl.col("ma20"))
                & (pl.col("close") > pl.col("ma20"))
                & (pl.col("momentum_5d") >= 0.05) & (pl.col("momentum_10d") >= 0.08)
                & (pl.col("close") >= pl.col("high_60d") * 0.90)
                & (pl.col("vol_ratio_5d") >= 0.70)
                & ~pl.col("symbol").str.starts_with("688")
                & ~pl.col("symbol").str.starts_with("300")
                & ~pl.col("symbol").str.starts_with("301")
                & ~pl.col("symbol").str.ends_with(".BJ")
            )
            cond = cond & ~pl.col("name").str.contains("(?i)ST|\\*ST|退")
            symbols = snap.filter(cond.fill_null(False)).sort("amount", descending=True).head(MAX_SYMBOLS)["symbol"].to_list()
        except Exception as exc:  # noqa: BLE001
            logger.warning("竞价路径采集候选池计算失败: %s", exc)
            symbols = []
        self._symbols_cache = (today, symbols)
        logger.info("竞价路径采集: 趋势候选 %d 只 (上限 %d)", len(symbols), MAX_SYMBOLS)
        return symbols

    def _load_day(self, today: date) -> bool:
        if self._day == today:
            return self._day_load_ok
        path = _path(Path(self._repo.store.data_dir), today)
        try:
            self._day_rows = pl.read_parquet(path).select(PATH_COLUMNS) if path.exists() else pl.DataFrame()
            self._day_load_ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("竞价路径当日历史读取失败，拒绝覆盖已有文件: %s", exc)
            self._day_rows = pl.DataFrame()
            self._day_load_ok = False
        self._day = today
        return self._day_load_ok

    def _depth_batch(self, symbols: list[str]) -> dict[str, dict]:
        state = self._app_state
        depth_service = getattr(state, "depth_service", None) if state else None
        fetch = getattr(depth_service, "_call_depth_batch", None)
        if not callable(fetch):
            logger.warning("竞价路径采集跳过: 目标五档批量路由不可用")
            return {}
        try:
            return fetch(symbols) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("竞价路径采集五档批量请求失败: %s", exc)
            return {}

    def collect_tick(self) -> dict:
        """采集当前拍；错过的拍不补采，返回本拍统计。"""
        if not self._repo:
            return {"ok": False, "reason": "repo_unavailable", "rows": 0}
        state = self._app_state
        capset = getattr(state, "capabilities", None) if state else None
        if capset is not None and not capset.has(Cap.DEPTH5_BATCH):
            return {"ok": False, "reason": "depth5_batch_unavailable", "rows": 0}

        today = cn_today()
        ts = cn_now().strftime("%H:%M:%S")
        if not (AUCTION_WINDOW_START <= ts <= AUCTION_WINDOW_END):
            return {"ok": False, "reason": "outside_auction_window", "rows": 0, "ts": ts}
        symbols = self._trend_symbols(today)
        if not symbols:
            return {"ok": False, "reason": "empty_candidate_pool", "rows": 0}
        depth_data = self._depth_batch(symbols)
        if not depth_data:
            return {"ok": False, "reason": "empty_depth", "rows": 0}

        vol_map: dict[str, tuple[float | None, float | None]] = {}
        quote_service = getattr(state, "quote_service", None) if state else None
        try:
            df, day = quote_service.get_enriched_today() if quote_service else self._repo.get_enriched_latest()
            if day == today and not df.is_empty() and "volume" in df.columns:
                cols = ["symbol", "volume"] + (["amount"] if "amount" in df.columns else [])
                for row in df.select(cols).iter_rows():
                    vol_map[str(row[0])] = (row[1], row[2] if len(row) > 2 else None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("竞价路径累计量读取失败: %s", exc)

        rows = []
        for symbol, depth in depth_data.items():
            bid_prices = list(depth.get("bid_prices") or []) + [None] * 5
            ask_prices = list(depth.get("ask_prices") or []) + [None] * 5
            bid_volumes = list(depth.get("bid_volumes") or []) + [None] * 5
            ask_volumes = list(depth.get("ask_volumes") or []) + [None] * 5
            cum_vol, cum_amount = vol_map.get(str(symbol), (None, None))
            row = {
                "date": today.isoformat(), "ts": ts, "symbol": symbol,
                "src_timestamp": depth.get("timestamp"),
                "quote_cum_vol": cum_vol, "quote_cum_amount": cum_amount,
            }
            for i in range(5):
                row.update({
                    f"bid_p{i + 1}": bid_prices[i], f"bid_v{i + 1}": bid_volumes[i],
                    f"ask_p{i + 1}": ask_prices[i], f"ask_v{i + 1}": ask_volumes[i],
                })
            rows.append(row)

        tick = pl.DataFrame(rows).select(PATH_COLUMNS)
        with self._lock:
            if not self._load_day(today):
                return {"ok": False, "reason": "history_read_failed", "rows": 0, "ts": ts}
            merged = pl.concat([self._day_rows, tick], how="diagonal_relaxed")
            merged = merged.unique(subset=["date", "ts", "symbol"], keep="last").sort(["ts", "symbol"])
            output = _path(Path(self._repo.store.data_dir), today)
            output.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_parquet(merged, output)
            self._day_rows = merged
        logger.info("竞价路径采集: %s 拍到 %d 只 (当日累计 %d 行)", ts, tick.height, merged.height)
        return {"ok": True, "reason": "ok", "rows": tick.height, "total_rows": merged.height, "ts": ts}


auction_path_service = AuctionPathService()
