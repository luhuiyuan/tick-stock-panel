"""把 qatdx 全历史行情导出成按年分卷的 csv.gz 备份包。

用途
----
把「初始化历史数据」这件每次重装都要花几小时的事变成一次性备份：导出后传网盘，
需要时用 ``restore_history_backup.py`` 灌回 ``data/`` 下的 parquet 分区。

产物结构
--------
::

    <out>/
      README.md
      manifest.json
      daily/kline_daily/<年>.csv.gz          A 股日K
      daily/kline_index_daily/<年>.csv.gz    指数日K
      daily/kline_etf_daily/<年>.csv.gz      ETF 日K
      minute/kline_minute_<周期>/<年>.csv.gz  分钟K
      corporate/xdxr.csv.gz                 除权除息原始事件(全历史一张)
      corporate/adj_factor.csv.gz           由 xdxr + 日K 离线推导的复权因子
      instruments/*.csv.gz                  标的名录

设计要点
--------
* **断点续跑**：DuckDB 中转库里有 ``done(table, symbol)``，中断后重跑自动跳过。
* **内存有界**：边抓边写中转库，最后按年 ``COPY`` 导出，不把全量堆在内存里。
* **量纲与项目一致**：日K 的 ``vol`` 是手；分钟接口回的是**股**，统一 ``/100`` 转手；
  ``amount`` 两边都是元。停牌日走项目同一套 ``filter_halt_days`` 剔除，
  恢复后与正常同步结果等价。
* **并发**：``qatdx.client`` 的 ``_call`` 是全局锁串行的，这里自己管连接 ——
  每个工作线程一条长连接，批量取完再交回主线程写库。

用法
----
::

    cd backend
    PYTHONPATH= uv run --no-sync python scripts/export_history_backup.py \
        --out D:/tsp/backup_export --workers 6

    # 先小样本验证链路
    ... --max-symbols 20 --out D:/tsp/backup_probe

    # 只重做某几张表(其余已完成的跳过)
    ... --tables kline_daily,minute_1m
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import duckdb
import polars as pl

from app.data_providers.normalizer import normalize_daily
from app.plugins.qatdx import client as qc

PAGE = 800
_REPO_ROOT = Path(__file__).resolve().parents[2]
# 日K 一次请求的根数上限。20800 是接口硬上限, 实测能覆盖 1990 年至今(8619 根)。
DAILY_BARS = 20800
# 分钟各周期的服务端上限, (表名, 周期, category, 根数)。15/30/60m 是服务器只给
# 500 个交易日(8000/4000/2000 根), 不是我们不想多取。
MINUTE_SPECS: tuple[tuple[str, str, int, int], ...] = (
    ("kline_minute_1m", "1m", 8, 20800),
    ("kline_minute_5m", "5m", 0, 20800),
    ("kline_minute_15m", "15m", 1, 8000),
    ("kline_minute_30m", "30m", 2, 4000),
    ("kline_minute_60m", "60m", 3, 2000),
)
DAILY_SPECS: tuple[tuple[str, str], ...] = (
    ("kline_daily", "stock"),
    ("kline_index_daily", "index"),
    ("kline_etf_daily", "etf"),
)

_DAILY_DDL = """
CREATE TABLE IF NOT EXISTS {table}(
    symbol VARCHAR, date DATE,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    volume DOUBLE, amount DOUBLE
)
"""
_MINUTE_DDL = """
CREATE TABLE IF NOT EXISTS {table}(
    symbol VARCHAR, datetime TIMESTAMP, date DATE,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    volume DOUBLE, amount DOUBLE
)
"""


# --------------------------------------------------------------------------- #
# 连接
# --------------------------------------------------------------------------- #
class Endpoint:
    """一台主站，每个工作线程各自持有一条长连接。"""

    def __init__(self, ip: str, port: int) -> None:
        self.ip = ip
        self.port = port
        self._local = threading.local()
        self._stat_lock = threading.Lock()
        self.reconnects = 0

    def api(self):
        api = getattr(self._local, "api", None)
        if api is not None:
            return api
        api = qc._import_api()(raise_exception=True)
        if not api.connect(self.ip, self.port, time_out=10.0):
            raise RuntimeError(f"连不上主站 {self.ip}:{self.port}")
        self._local.api = api
        return api

    def drop(self) -> None:
        api = getattr(self._local, "api", None)
        self._local.api = None
        if api is not None:
            try:
                api.disconnect()
            except Exception:  # noqa: BLE001
                pass
        with self._stat_lock:
            self.reconnects += 1


class EndpointPool(Endpoint):
    """多台主站轮转，每个线程认领一条长连接。

    单台主站挂 8 条连接持续拉全历史会被限速（实测从 5.2 只/秒掉到 0.9 只/秒），
    分散到多台主站才能跑满。主站池有 100+ 台，但只有一部分存了 1990 年代的深数据，
    所以先用 ``pick_endpoints`` 筛出合格的再分配。
    """

    def __init__(self, endpoints: Sequence[tuple[str, int]]) -> None:
        if not endpoints:
            raise ValueError("没有可用主站")
        self.endpoints = list(endpoints)
        self.ip, self.port = self.endpoints[0]
        self._local = threading.local()
        self._stat_lock = threading.Lock()
        self._counter = 0
        self.reconnects = 0

    def api(self):
        api = getattr(self._local, "api", None)
        if api is not None:
            return api
        with self._stat_lock:
            ip, port = self.endpoints[self._counter % len(self.endpoints)]
            self._counter += 1
        api = qc._import_api()(raise_exception=True)
        if not api.connect(ip, port, time_out=10.0):
            raise RuntimeError(f"连不上主站 {ip}:{port}")
        self._local.api = api
        self._local.addr = (ip, port)
        return api

    def addr(self) -> tuple[str, int]:
        return getattr(self._local, "addr", (self.ip, self.port))


def _probe_depth(ep: tuple[str, int], *, code: str = "600601", market: int = 1) -> bool:
    """这台主站存不存 1990 年代的深历史。

    用 600601(1990-12-19 上市, 8619 根) 的第 11 页当探针: 有深历史的主站
    这一页落在 1990-1993, 没有的返回空或还是近几年的数据。
    """
    ip, port = ep
    api = None
    try:
        api = qc._import_api()(raise_exception=True)
        if not api.connect(ip, port, time_out=5.0):
            return False
        chunk = api.get_security_bars(9, market, code, 8000, 800) or []
        if not chunk:
            return False
        year = min(int(row.get("year") or 9999) for row in chunk)
        return year <= 1995
    except Exception:  # noqa: BLE001
        return False
    finally:
        if api is not None:
            try:
                api.disconnect()
            except Exception:  # noqa: BLE001
                pass


def pick_endpoints(want: int) -> list[tuple[str, int]]:
    """挑 want 台可用且存了深历史的主站。"""
    candidates = qc._server_pool()
    reachable: list[tuple[str, int]] = []
    with ThreadPoolExecutor(max_workers=32) as pool:
        futures = {pool.submit(qc._tcp_latency_ms, ip, port, 2.0): (ip, port) for ip, port in candidates}
        for future in as_completed(futures):
            try:
                if future.result() is not None:
                    reachable.append(futures[future])
            except Exception:  # noqa: BLE001
                continue
    print(f"主站：池 {len(candidates)} 台，TCP 通 {len(reachable)} 台，正在筛深历史…", flush=True)

    qualified: list[tuple[str, int]] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(_probe_depth, ep): ep for ep in reachable}
        for future in as_completed(futures):
            if future.result():
                qualified.append(futures[future])
    if not qualified:
        print("  没有主站通过深历史探测，退回默认选择", flush=True)
        return [qc._select_server()]
    qualified.sort()
    chosen = qualified[:want] if want else qualified
    print(f"  深历史合格 {len(qualified)} 台，选用 {len(chosen)} 台:", flush=True)
    for ip, port in chosen:
        print(f"    {ip}:{port}", flush=True)
    return chosen


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stamp(row: dict) -> datetime | None:
    try:
        return datetime(
            int(row["year"]), int(row["month"]), int(row["day"]),
            int(row.get("hour") or 0), int(row.get("minute") or 0),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _bars(ep: Endpoint, market: int, code: str, category: int, count: int, *, index: bool) -> list[dict]:
    """按 800 一页从最新往回翻，凑满 count 根。"""
    api = ep.api()
    fetch = api.get_index_bars if index else api.get_security_bars
    rows: list[dict] = []
    start = 0
    while len(rows) < count:
        chunk = fetch(category, market, code, start, PAGE) or []
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < PAGE:
            break
        start += PAGE
    qc._clean_volumes(rows)
    return rows


def _dedup(rows: Iterable[dict], key: Callable[[dict], Any]) -> list[dict]:
    """分页边界会重叠，按时间戳去重并保持升序。"""
    seen: dict[Any, dict] = {}
    for row in rows:
        stamp = key(row)
        if stamp is not None:
            seen[stamp] = row
    return [seen[k] for k in sorted(seen)]


# --------------------------------------------------------------------------- #
# 行构造
# --------------------------------------------------------------------------- #
def daily_frame(rows: Sequence[dict], symbol: str) -> pl.DataFrame:
    out: list[dict] = []
    for row in _dedup(rows, _stamp):
        stamp = _stamp(row)
        if stamp is None:
            continue
        out.append({
            "symbol": symbol,
            "date": stamp.date(),
            "open": _f(row.get("open")),
            "high": _f(row.get("high")),
            "low": _f(row.get("low")),
            "close": _f(row.get("close")),
            "volume": _f(row.get("vol", row.get("volume"))),
            "amount": _f(row.get("amount")),
        })
    if not out:
        return pl.DataFrame()
    return normalize_daily(out, default_symbol=symbol).select(
        "symbol", "date", "open", "high", "low", "close", "volume", "amount"
    )


def minute_frame(rows: Sequence[dict], symbol: str) -> pl.DataFrame:
    out: list[dict] = []
    for row in _dedup(rows, _stamp):
        stamp = _stamp(row)
        if stamp is None:
            continue
        volume = _f(row.get("vol", row.get("volume")))
        out.append({
            "symbol": symbol,
            "datetime": stamp,
            "date": stamp.date(),
            # 分钟接口回的成交量是**股**，项目 kline_minute.volume 是手。
            "open": _f(row.get("open")),
            "high": _f(row.get("high")),
            "low": _f(row.get("low")),
            "close": _f(row.get("close")),
            "volume": None if volume is None else volume / 100.0,
            "amount": _f(row.get("amount")),
        })
    if not out:
        return pl.DataFrame()
    frame = pl.DataFrame(out).with_columns(
        pl.col("open").cast(pl.Float64, strict=False),
        pl.col("high").cast(pl.Float64, strict=False),
        pl.col("low").cast(pl.Float64, strict=False),
        pl.col("close").cast(pl.Float64, strict=False),
        pl.col("volume").cast(pl.Float64, strict=False),
        pl.col("amount").cast(pl.Float64, strict=False),
    )
    # 与日K 同一套停牌口径：无成交、无成交额的行不是真实交易日。
    halted = (pl.col("volume").fill_null(0) == 0) & (pl.col("amount").fill_null(0) == 0)
    return frame.filter(~halted).sort("datetime")


def xdxr_frame(rows: Sequence[dict], symbol: str) -> pl.DataFrame:
    out: list[dict] = []
    for row in rows:
        stamp = _stamp(row)
        if stamp is None:
            continue
        out.append({
            "symbol": symbol,
            "ex_date": stamp.date(),
            "category": _f(row.get("category")),
            "fenhong": _f(row.get("fenhong")),
            "songzhuangu": _f(row.get("songzhuangu")),
            "peigu": _f(row.get("peigu")),
            "peigujia": _f(row.get("peigujia")),
            "suogu": _f(row.get("suogu")),
            "panqianliutong": _f(row.get("panqianliutong")),
            "panhouliutong": _f(row.get("panhouliutong")),
            "qianzongguben": _f(row.get("qianzongguben")),
            "houzongguben": _f(row.get("houzongguben")),
        })
    if not out:
        return pl.DataFrame()
    return pl.DataFrame(out).unique(subset=["symbol", "ex_date", "category"], keep="last").sort("ex_date")


# --------------------------------------------------------------------------- #
# 中转库
# --------------------------------------------------------------------------- #
class Store:
    """DuckDB 中转库 + 断点记录。写入串行（DuckDB 单写者）。"""

    def __init__(self, path: Path, *, fresh: bool) -> None:
        if fresh and path.exists():
            path.unlink()
        self.path = path
        self.con = duckdb.connect(str(path))
        self.con.execute("PRAGMA threads=2")
        self.con.execute("PRAGMA memory_limit='4GB'")
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS done(table_name VARCHAR, symbol VARCHAR, rows BIGINT, finished_at TIMESTAMP)"
        )
        for table, kind in DAILY_SPECS:
            self.con.execute(_DAILY_DDL.format(table=table))
        for table, _freq, _cat, _n in MINUTE_SPECS:
            self.con.execute(_MINUTE_DDL.format(table=table))
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS xdxr(
                symbol VARCHAR, ex_date DATE, category DOUBLE,
                fenhong DOUBLE, songzhuangu DOUBLE, peigu DOUBLE, peigujia DOUBLE, suogu DOUBLE,
                panqianliutong DOUBLE, panhouliutong DOUBLE,
                qianzongguben DOUBLE, houzongguben DOUBLE
            )
            """
        )
        self._done: dict[str, set[str]] = {}
        for table, symbol in self.con.execute("SELECT table_name, symbol FROM done").fetchall():
            self._done.setdefault(table, set()).add(symbol)

    def finished(self, table: str) -> set[str]:
        return self._done.setdefault(table, set())

    def write(self, table: str, frame: pl.DataFrame, *, symbol: str) -> int:
        if frame is None or frame.is_empty():
            self.con.execute(
                "INSERT INTO done VALUES (?, ?, 0, ?)", [table, symbol, datetime.now()]
            )
            self._done.setdefault(table, set()).add(symbol)
            return 0
        self.con.register("_incoming", frame.to_arrow())
        cols = ", ".join(frame.columns)
        self.con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _incoming")
        self.con.unregister("_incoming")
        self.con.execute(
            "INSERT INTO done VALUES (?, ?, ?, ?)", [table, symbol, frame.height, datetime.now()]
        )
        self._done.setdefault(table, set()).add(symbol)
        return frame.height

    def count(self, table: str) -> int:
        return int(self.con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #
@dataclass
class Job:
    table: str
    symbol: str
    market: int
    code: str
    kind: str  # daily | minute | xdxr
    category: int = 9
    bars: int = DAILY_BARS


def run_job(job: Job, ep: Endpoint) -> pl.DataFrame:
    if job.kind == "xdxr":
        api = ep.api()
        rows = list(api.get_xdxr_info(job.market, job.code) or [])
        return xdxr_frame(rows, job.symbol)
    raw = _bars(ep, job.market, job.code, job.category, job.bars, index=(job.kind == "index"))
    if job.kind == "minute":
        return minute_frame(raw, job.symbol)
    return daily_frame(raw, job.symbol)


def _with_retry(fn: Callable[[], pl.DataFrame], ep: Endpoint, *, attempts: int = 3) -> tuple[pl.DataFrame, str | None]:
    last: str | None = None
    for i in range(attempts):
        try:
            return fn(), None
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            ep.drop()
            time.sleep(0.4 * (i + 1))
    return pl.DataFrame(), last


def collect(store: Store, jobs: list[Job], ep: Endpoint, *, workers: int, label: str) -> dict[str, Any]:
    total = len(jobs)
    stats = {"table": label, "jobs": total, "rows": 0, "failed": []}
    if not total:
        return stats

    started = time.time()
    done = 0
    pending: list[tuple[Job, pl.DataFrame]] = []

    def flush() -> None:
        for job, frame in pending:
            store.write(job.table, frame, symbol=job.symbol)
        pending.clear()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # 分批投递: 一次把 5225 个任务全塞进 executor, 未消费的结果会一直堆在内存里
        # (实测跑 1000 只后进程被 OOM 掉, 日志无 traceback)。每批只放 workers*4 个。
        batch_size = max(workers * 4, 8)
        for offset in range(0, len(jobs), batch_size):
            batch = jobs[offset:offset + batch_size]
            futures = {pool.submit(_with_retry, lambda j=job: run_job(j, ep), ep): job for job in batch}
            for future in as_completed(futures):
                job = futures[future]
                frame, error = future.result()
                if error:
                    stats["failed"].append({"symbol": job.symbol, "error": error})
                pending.append((job, frame))
                if frame is not None and not frame.is_empty():
                    stats["rows"] += frame.height
                done += 1
                if len(pending) >= 100:
                    flush()
                if done % 100 == 0 or done == total:
                    rate = done / max(time.time() - started, 1e-6)
                    eta = (total - done) / rate if rate else 0
                    print(
                        f"  [{label}] {done}/{total} ({rate:.2f} 只/秒, 剩 {eta / 60:.0f} 分钟) "
                        f"行数={stats['rows']:,} 失败={len(stats['failed'])}",
                        flush=True,
                    )
    flush()
    stats["seconds"] = round(time.time() - started, 1)
    return stats


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #
def export_table(store: Store, out_root: Path, table: str, year_col: str, sub: str) -> list[dict]:
    rows = store.con.execute(
        f"SELECT DISTINCT year({year_col}) FROM {table} WHERE {year_col} IS NOT NULL ORDER BY 1"
    ).fetchall()
    years = [int(r[0]) for r in rows]
    if not years:
        return []
    target = out_root / sub
    target.mkdir(parents=True, exist_ok=True)
    order = "symbol, " + year_col
    out: list[dict] = []
    for year in years:
        dst = target / f"{year}.csv.gz"
        if dst.exists():
            dst.unlink()
        store.con.execute(
            f"""
            COPY (
                SELECT * FROM {table}
                WHERE year({year_col}) = {year}
                ORDER BY {order}
            ) TO '{dst.as_posix()}' (FORMAT CSV, HEADER, COMPRESSION GZIP)
            """
        )
        n = store.con.execute(f"SELECT count(*) FROM {table} WHERE year({year_col}) = {year}").fetchone()[0]
        out.append({
            "file": str(dst.relative_to(out_root)).replace("\\", "/"),
            "rows": int(n),
            "bytes": dst.stat().st_size,
        })
        print(f"  导出 {dst.name}: {n:,} 行 {dst.stat().st_size / 1e6:.1f} MB", flush=True)
    return out


def write_readme(out_root: Path, manifest: dict) -> None:
    lines = [
        "# A股历史行情备份包",
        "",
        f"生成时间：{manifest['generated_at']}",
        f"数据源：qatdx（通达信行情主站）",
        "",
        "## 覆盖范围",
        "",
        "| 数据 | 范围 | 说明 |",
        "|---|---|---|",
        "| 日K（股票/指数/ETF） | 1990-12-19 | 全历史，一次请求 20800 根 |",
        "| 除权除息 xdxr | 全历史 | 原始事件，含分红送转配股 |",
        "| 复权因子 | 由 xdxr + 日K 离线推导 | 与项目 adj_factor 同算法 |",
        "| 1 分钟 | **2000-06-09 起** | 逐股逐日调 `get_history_minute_time_data`，按年打包 `minute/kline_minute_1m/<year>.zip` |",
        "| 逐笔（3 秒快照） | **2000-06-09 起** | `get_history_transaction_data`，按年打包 `<year>_ticks.zip` |",
        "| 5/15/30/60 分钟 | 不在本包 | K 线通道上限 20800 根，需要时由 1 分钟重采样 |",
        "",
        "> 分钟与逐笔的真实下限都是 **2000-06-09**（2000-06-08 及以前所有主站返回空）。",
        "> 这个下限是**主站级差异**——111 台主站里只有 23 台存有 2000 年数据，取老日期",
        "> 前必须按目标年份筛主站，否则会静默拿到空表。",
        ">",
        "> 别把两条通道混为一谈：`get_security_bars`（K 线通道）只能取最近 87 个交易日",
        "> （20800 根上限），那是 K 线通道自身的限制，与分时通道无关。",
        "",
        "## 单位",
        "",
        "- `volume`：**手**（分钟接口原样返回股，已统一 /100）",
        "- `amount`：元",
        "- 停牌日（`open=high=0` 或量额为 0）已剔除，与项目正常同步口径一致",
        "",
        "## 恢复",
        "",
        "```bash",
        "cd backend",
        "PYTHONPATH= uv run --no-sync python scripts/restore_history_backup.py \\",
        "    --src <本备份包目录> --data ../data",
        "```",
        "",
        "恢复落位：",
        "",
        "| 备份 | 项目落位 |",
        "|---|---|",
        "| `daily/kline_daily` | `data/kline_daily/date=*/part.parquet` |",
        "| `daily/kline_index_daily` | `data/kline_index_daily/` |",
        "| `daily/kline_etf_daily` | `data/kline_etf_daily/` |",
        "| `minute/kline_minute_1m/<year>.zip` | `data/kline_minute/`（项目主用的分钟表） |",
        "| `corporate/xdxr` | `data/xdxr/`（原始事件，因子由项目重算） |",
        "",
        "> 两种打包形态别混：`daily/` 与 `corporate/` 是**按年一个 `csv.gz`**（该年所有",
        "> 标的混装在一个文件里），`restore_history_backup.py` 直接吃这种形态；",
        "> `minute/*.zip` 与 `*_ticks.zip` 是**一只股票一个 csv 成员**，目前没有一键",
        "> 恢复脚本，需要时解压后按 `symbol` 分组写入对应 parquet 分区。",
        "",
        "> 复权因子 `adj_factor` 不在备份里：它由 `xdxr` + 日K 离线推导，是**派生数据**，",
        "> 项目下次同步会自动重算，没必要占备份体积。",
        "",
        "## 文件清单",
        "",
        "| 文件 | 行数 | 大小 |",
        "|---|---|---|",
    ]
    for item in manifest["files"]:
        lines.append(f"| `{item['file']}` | {item['rows']:,} | {item['bytes'] / 1e6:.1f} MB |")
    (out_root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def load_symbols(data_dir: Path) -> tuple[list[tuple[str, int, str]], list[tuple[str, int, str]], list[tuple[str, int, str]]]:
    """从 data/ 的维表读三类标的。返回 [(symbol, market, code)]。"""
    con = duckdb.connect()
    out = []
    for table in ("instruments", "instruments_index", "instruments_etf"):
        path = data_dir / table
        files = sorted(p.as_posix() for p in path.rglob("*.parquet")) if path.exists() else []
        items: list[tuple[str, int, str]] = []
        if files:
            rows = con.execute(f"SELECT DISTINCT symbol FROM read_parquet({files!r}) ORDER BY 1").fetchall()
            for (symbol,) in rows:
                parsed = qc.split_symbol(symbol)
                if parsed is None:
                    continue
                items.append((symbol, parsed[0], parsed[1]))
        out.append(items)
    con.close()
    return out[0], out[1], out[2]


def plan_jobs(
    tables: set[str],
    stocks: Sequence[tuple[str, int, str]],
    indices: Sequence[tuple[str, int, str]],
    etfs: Sequence[tuple[str, int, str]],
    store: Store,
) -> list[Job]:
    jobs: list[Job] = []
    pools = {"kline_daily": stocks, "kline_index_daily": indices, "kline_etf_daily": etfs}
    if "kline_daily" in tables or "xdxr" in tables:
        done = store.finished("kline_daily")
        if "kline_daily" in tables:
            jobs += [Job("kline_daily", s, m, c, "daily", 9, DAILY_BARS) for s, m, c in stocks if s not in done]
        if "xdxr" in tables:
            done_x = store.finished("xdxr")
            jobs += [Job("xdxr", s, m, c, "xdxr") for s, m, c in stocks if s not in done_x]
    for table, _kind in DAILY_SPECS[1:]:
        if table not in tables:
            continue
        kind = "index" if table == "kline_index_daily" else "daily"
        done = store.finished(table)
        jobs += [Job(table, s, m, c, kind, 9, DAILY_BARS) for s, m, c in pools[table] if s not in done]
    for table, freq, category, bars in MINUTE_SPECS:
        if table not in tables:
            continue
        done = store.finished(table)
        jobs += [Job(table, s, m, c, "minute", category, bars) for s, m, c in stocks if s not in done]
    return jobs


def group(jobs: list[Job]) -> dict[str, list[Job]]:
    out: dict[str, list[Job]] = {}
    for job in jobs:
        out.setdefault(job.table, []).append(job)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 qatdx 全历史行情为按年 csv.gz 备份包")
    parser.add_argument("--out", type=Path, default=Path("D:/tsp/backup_export"), help="输出根目录")
    parser.add_argument("--data", type=Path, default=_REPO_ROOT / "data", help="项目的 data 目录(读维表)")
    parser.add_argument("--workers", type=int, default=8, help="并发线程数")
    parser.add_argument("--endpoints", type=int, default=8, help="分散到几台主站(单台会被限速)")
    parser.add_argument("--max-symbols", type=int, default=0, help="调试用：每类标的最多取几只")
    parser.add_argument(
        "--tables",
        default="all",
        help="逗号分隔的表名；all=全部。可选: " + ",".join(
            [t for t, _ in DAILY_SPECS] + ["xdxr"] + [t for t, *_ in MINUTE_SPECS]
        ),
    )
    parser.add_argument("--fresh", action="store_true", help="清空中转库重来")
    parser.add_argument("--skip-export", action="store_true", help="只抓不导出")
    args = parser.parse_args(argv)

    all_tables = {t for t, _ in DAILY_SPECS} | {"xdxr"} | {t for t, *_ in MINUTE_SPECS}
    tables = all_tables if args.tables.strip().lower() == "all" else {
        t.strip() for t in args.tables.split(",") if t.strip()
    }
    unknown = tables - all_tables
    if unknown:
        print(f"未知表名: {sorted(unknown)}", file=os.sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    staging = args.out / "_staging"
    staging.mkdir(parents=True, exist_ok=True)
    store = Store(staging / "backup.duckdb", fresh=args.fresh)

    stocks, indices, etfs = load_symbols(args.data)
    if args.max_symbols:
        stocks = stocks[: args.max_symbols]
        indices = indices[: args.max_symbols]
        etfs = etfs[: args.max_symbols]
    print(f"标的：A股 {len(stocks)} / 指数 {len(indices)} / ETF {len(etfs)}")

    endpoints = pick_endpoints(args.endpoints)
    ep = EndpointPool(endpoints)

    jobs = plan_jobs(tables, stocks, indices, etfs, store)
    print(f"待抓取任务 {len(jobs):,} 个（已完成的不重复）")

    summaries = []
    started = time.time()
    # 日K 先跑：它是恢复时的主料，也最快出结果。
    order = [t for t, _ in DAILY_SPECS] + ["xdxr"] + [t for t, *_ in MINUTE_SPECS]
    grouped = group(jobs)
    for table in order:
        chunk = grouped.get(table, [])
        if not chunk:
            continue
        print(f"\n== {table} ({len(chunk):,} 只) ==", flush=True)
        summaries.append(collect(store, chunk, ep, workers=args.workers, label=table))

    print(f"\n抓取完成，用时 {(time.time() - started) / 60:.1f} 分钟")

    manifest: dict[str, Any] = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "endpoints": [f"{ip}:{port}" for ip, port in endpoints],
        "summaries": summaries,
        "tables": {},
        "files": [],
    }
    if not args.skip_export:
        print("\n== 按年导出 csv.gz ==")
        sub_plan = {
            **{t: f"daily/{t}" for t, _ in DAILY_SPECS},
            **{t: f"minute/{t}" for t, *_ in MINUTE_SPECS},
            "xdxr": "corporate/xdxr",
        }
        for table, sub in sub_plan.items():
            # 只导出本次跑到的表：中转库里可能有上次留下的数据，别重复刷文件。
            if table not in tables:
                continue
            year_col = "ex_date" if table == "xdxr" else "date"
            files = export_table(store, args.out, table, year_col, sub)
            manifest["tables"][table] = {"files": len(files), "rows": sum(f["rows"] for f in files)}
            manifest["files"] += files
        write_readme(args.out, manifest)

    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    total_rows = sum(t["rows"] for t in manifest["tables"].values())
    print(f"\n完成：{len(manifest['files'])} 个文件，共 {total_rows:,} 行 → {args.out}")
    failed = [f for s in summaries for f in s["failed"]]
    if failed:
        print(f"失败 {len(failed)} 只（重跑同一命令会自动补）：")
        for item in failed[:10]:
            print(f"  {item['symbol']}: {item['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
