"""按年导出**完整历史分钟数据**（2000-06-09 起），一次抓取产出两层。

为什么是 2000-06-09
-------------------
K 线接口 ``get_security_bars`` 的 1 分钟只有最近 87 个交易日（``_MAX_BARS`` 20800 / 240），
更早的拿不到。走逐笔接口 ``get_history_transaction_data`` 按**日期**取数可以绕开这个
上限，实测下限是 **2000-06-09**：逐日往回探，2000-06-08 及之前全是 0 根，2000-06-09
起每天 240 点，跨标的（000001/000002/600000/600601/000009）与跨市场一致。

深度还是**主站级**差异：主站池 111 台里只有 23 台有 2000 年数据，6 台对同一天返回空表。
所以每年开跑前必须按该年份的真实交易日重新筛一遍主站（``_probe_ticks``），否则会
整年 0 行、白跑几小时。

两层产出
--------
每一年产出两个 zip，内部都是**一只股票一个 csv**（纯文本，zip 就是压缩层，解压
一次即得 csv，不用二次解压）：

1. **分钟增强表** ``minute/kline_minute_1m/<year>.zip``
   成员 ``<symbol>.csv``，列
   ``symbol,datetime,date,open,high,low,close,volume,amount,buy_volume,sell_volume``
   OHLCV 与官方 1 分钟 K 对得上（240 根里 236 根完全一致，差异只在 09:30 竞价、
   11:29、14:59 三个边界）。``buy_volume`` / ``sell_volume`` 是从逐笔 ``buyorsell``
   拆出来的主动买卖量，这是逐笔相对分钟 K 唯一会丢的信息，做资金流指标要用。
2. **逐笔原层** ``<ticks-root>/<year>_ticks.zip``
   成员 ``<symbol>.csv``，列 ``symbol,date,time,price,volume,direction``。原样保留，
   可重聚合成任意 >=3 秒的周期。注意数据源的「逐笔」是**每 3 秒一个成交汇总**（实测
   现代个股正好 4800 笔/日 = 14400 秒 / 3；单分钟最多 20 条、从未超过，满格分钟
   26048 个），没有委托队列、没有撤单，做不了订单流微观结构研究。

   由此与官方日K 的偏差（2025 年 30 只 / 7290 个股票日全样本实测，均为数据源粒度
   所致，非抓取问题）：开盘价、收盘价 **100% 精确**；成交量 61.25% 相等、其余恒差
   **+1 手**（官方对总量向下取整，逐笔四舍五入后累加）；最高/最低价只有
   70.77% / 75.84% 相等、最大差 0.4 元（**切片只保留末价，切片内极值被丢弃**，要
   精确 high/low 用日K）；成交额相对误差中位 0.0078% / 最大 0.26%。

   ``direction`` 是通达信按成交价与盘口位置推断的方向，**不是交易所标记**：
   ``0`` 主动买、``1`` 主动卖、``2`` 中性（含 09:25 开盘竞价、15:00 收盘竞价，也含
   切片内买卖量恰好相等、无法判向的成交）、``8`` 集合竞价匹配（09:15~09:24 虚拟
   撮合、14:57~14:59 收盘竞价撮合，无主动方）。聚合买卖量时 ``2`` 与 ``8`` 都按中性
   处理，不计入任何一边。

   **``8`` 的成交量恒为 0 而价格是试探价，用价格算任何指标前必须过滤**（实测未过滤
   时开盘价只有 49.93% 对得上，过滤后 100%）。另有少量 0 手但价格正常的记录
   （dir 为 0/1，全样本 28 条 / 30 只·年），算笔数与笔均量前要一并滤掉。

打包成功后会删掉逐笔分片和年度中转库，本地只留两个 zip；传完网盘即可清理，重跑
同一命令会从「该年已完成」直接跳过。

为什么按「整只股票对齐的分片」提交
--------------------------------
一次抓取要跑好几天，中途必然中断。如果按「股票」记完成标记，崩在第 N 只时会留下
「逐笔已写、标记未落」的窗口，重跑就产生重复行。这里把若干**整只股票**打成一个
分片（shard），分片是原子单位：崩了就把整个分片重抓重写（一只股票一年约 27 秒，
一个分片几十秒），永远不会有重复。

分片名由「按估计字节累加」确定性地算出来，所以重跑时同一批股票一定落到同一个
``part-XXXX`` 上。

用法
----
::

    cd backend
    # 先看计划，不动网络
    PYTHONPATH= uv run --no-sync python scripts/export_history_minute.py --dry-run --from-year 2000

    # 跑一年试水
    PYTHONPATH= uv run --no-sync python scripts/export_history_minute.py --years 2000

    # 全量
    PYTHONPATH= uv run --no-sync python scripts/export_history_minute.py --from-year 2000
"""
from __future__ import annotations

import argparse
import json
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

import duckdb
import polars as pl

from app.plugins.qatdx import client as qc
from app.plugins.qatdx.provider import _ticks_to_bars

MINUTE_TABLE = "kline_minute_1m"
# 逐笔/分时接口的实测下限：2000-06-08 及之前全空，2000-06-09 起每天 240 点。
TICKS_SINCE_YEAR = 2000
TICKS_FLOOR = date(2000, 6, 9)
# 实测（70,484 笔真实数据压出来）：原始 35.8 字节/笔、gzip 后 4.2 字节/笔。
BYTES_PER_TICK_RAW = 36.0
BYTES_PER_TICK_GZ = 4.2
# 每个 csv 成员塞进 zip 时的 deflate 级别。内容已是纯文本，6 是速度与体积的平衡点。
ZIP_COMPRESSLEVEL = 6


# --------------------------------------------------------------------------- #
# 连接池
# --------------------------------------------------------------------------- #
class Endpoint:
    """多主站轮转，每线程一条长连接。"""

    def __init__(self, endpoints: Sequence[tuple[str, int]]) -> None:
        self.endpoints = list(endpoints)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._counter = 0
        self.ip, self.port = self.endpoints[0]

    def api(self):
        api = getattr(self._local, "api", None)
        if api is not None:
            return api
        with self._lock:
            ip, port = self.endpoints[self._counter % len(self.endpoints)]
            self._counter += 1
        api = qc._import_api()(raise_exception=True)
        if not api.connect(ip, port, time_out=10.0):
            raise RuntimeError(f"连不上主站 {ip}:{port}")
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


def _probe_depth(ep: tuple[str, int], *, code: str = "600601", market: int = 1) -> bool:
    """这台主站的**日K**能回到 1991（深历史主站的粗筛）。"""
    ip, port = ep
    api = None
    try:
        api = qc._import_api()(raise_exception=True)
        if not api.connect(ip, port, time_out=5.0):
            return False
        chunk = api.get_security_bars(9, market, code, 8000, 800) or []
        return bool(chunk) and min(int(r.get("year") or 9999) for r in chunk) <= 1995
    except Exception:  # noqa: BLE001
        return False
    finally:
        if api is not None:
            try:
                api.disconnect()
            except Exception:  # noqa: BLE001
                pass


def _probe_ticks(ep: tuple[str, int], ymds: Sequence[int], *, code: str = "000001",
                 market: int = 0) -> bool:
    """这台主站**该年份**有没有逐笔数据。

    日K 的深历史筛选不能代表逐笔：实测有主站日K 能回到 1991，逐笔却对 2000 年
    返回空表。每年开跑前按该年的**真实交易日**重新验一遍，否则整年白跑。

    传的是候选日列表而不是单日，因为单日可能撞上停牌或落在数据下限之前
    （2000-06-09 之前全年为空），那样会把所有主站都误判成不合格。
    """
    ip, port = ep
    api = None
    try:
        api = qc._import_api()(raise_exception=True)
        if not api.connect(ip, port, time_out=5.0):
            return False
        for ymd in ymds:
            chunk = api.get_history_transaction_data(market, code, 0, qc._TICK_PAGE, ymd) or []
            if chunk:
                return True
        return False
    except Exception:  # noqa: BLE001
        return False
    finally:
        if api is not None:
            try:
                api.disconnect()
            except Exception:  # noqa: BLE001
                pass


def _tcp_ok(ep: tuple[str, int]) -> bool:
    try:
        return qc._tcp_latency_ms(ep[0], ep[1], 2.0) is not None
    except Exception:  # noqa: BLE001
        return False


def pick_endpoints(want: int, *, probe_ymds: Sequence[int]) -> list[tuple[str, int]]:
    """筛出「该年份确实有逐笔」的主站。没有合格主站时直接抛错，不做兜底。"""
    pool = qc._server_pool()
    reachable: list[tuple[str, int]] = []
    with ThreadPoolExecutor(max_workers=32) as ex:
        futures = {ex.submit(_tcp_ok, ep): ep for ep in pool}
        for future in as_completed(futures):
            if future.result():
                reachable.append(futures[future])
    deep: list[tuple[str, int]] = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = {ex.submit(_probe_depth, ep): ep for ep in reachable}
        for future in as_completed(futures):
            if future.result():
                deep.append(futures[future])
    qualified: list[tuple[str, int]] = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = {ex.submit(_probe_ticks, ep, probe_ymds): ep for ep in deep}
        for future in as_completed(futures):
            if future.result():
                qualified.append(futures[future])
    if not qualified:
        raise RuntimeError(
            f"没有主站对 {list(probe_ymds)} 提供逐笔数据"
            f"（TCP 通 {len(reachable)} / 日K 深 {len(deep)}）"
        )
    qualified.sort()
    return qualified[:want]


def probe_days(calendar: dict[str, list[date]]) -> list[int]:
    """挑几个「一定该有数据」的日子去验主站。

    不能用年内**最早**交易日：2000 年的最早交易日是 01-04，早于逐笔数据下限
    2000-06-09，全年为空，会把所有主站误判成不合格。这里取年内**最后几个**
    交易日，并用 000001（1991 年上市、至今未停牌）当探针标的。
    """
    anchor = calendar.get("000001.SZ")
    if anchor:
        days = [day for day, _ in anchor[-3:]]
    else:
        days = sorted({day for rows in calendar.values() for day, _ in rows})[-3:]
    return [int(d.strftime("%Y%m%d")) for d in reversed(days)]


# --------------------------------------------------------------------------- #
# 抓取
# --------------------------------------------------------------------------- #
@dataclass
class Job:
    symbol: str
    market: int
    code: str
    days: tuple[tuple[date, int, float], ...]


# 单日抓取的重试次数：每次换一台主站（``Endpoint.drop()`` 之后下一次连接会轮转到
# 池里的下一台），4 次还取不全就说明这天在这批主站上真的拿不到，交给上层记失败。
_DAY_ATTEMPTS = 4
# 逐笔成交量与日K 的允许偏差（手）：日K 对总量向下取整、逐笔按笔累加，实测 19,239
# 个股票日的差值只可能是 0 或 +1，取 2 留一点余量。
_VOLUME_TOLERANCE = 2.0


class IncompleteDayError(RuntimeError):
    """日历上是交易日，主站却返回空表或取不全的逐笔。"""


def _read_day(api, market: int, code: str, ymd: int) -> list[dict]:
    """一天一股取完全部逐笔。``offset`` 从当日最新一笔往回累加，页内升序。"""
    pages: list[list[dict]] = []
    offset = 0
    for _ in range(qc._TICK_MAX_PAGES):
        chunk = list(api.get_history_transaction_data(market, code, offset, qc._TICK_PAGE, ymd) or [])
        if not chunk:
            break
        pages.append(chunk)
        offset += len(chunk)
        if len(chunk) < qc._TICK_PAGE or str(chunk[0].get("time") or "") <= "09:16":
            break
    return [row for page in reversed(pages) for row in page]


def _session_volume(rows: Sequence[dict]) -> float:
    """当日**盘内**成交量（手），口径与日K 对齐。

    科创板/创业板的盘后固定价格交易（15:05~15:30）出现在逐笔里、但不在日K 里，
    不剔掉的话这类票会恒超几千手（实测 688278.SH 2025-04-10 差 4,001 手，正好是
    15:18 的 1,000 手 + 15:19 的 3,000 手 + 舍入）。分钟表同样不含这一段
    （``_ticks_to_bars`` 丢掉 ``minute_of_day > 900``）。
    """
    return sum(float(r.get("vol") or 0.0) for r in rows if str(r.get("time") or "") <= "15:00")


def fetch_ticks(ep: "Endpoint", market: int, code: str,
                days: Sequence[tuple[date, int, float]]) -> dict[date, list[dict]]:
    """把一只股票一年的逐笔取全，**取不全就抛错**。

    日历来自日K，列出的每一天都确实成交过，所以「返回空表」只能是主站抖动
    （限流/缓存），不是当天没数据。这里以前遇到空页直接 ``break``，2025 年就这样
    静默丢了 460 个股票日（全部落在 01-10 一天），分片还照常被标记完成。
    成交量校验同时覆盖「页尾截断」——非空但不完整同样算失败。
    """
    out: dict[date, list[dict]] = {}
    for day, ymd, want in days:
        rows: list[dict] | None = None
        reason = ""
        for attempt in range(1, _DAY_ATTEMPTS + 1):
            chunk = _read_day(ep.api(), market, code, ymd)
            if not chunk:
                # 日K 当日成交量为 0（停牌但有行、不足一手的零成交）时，空表就是正确答案。
                if want <= 0:
                    rows = []
                    break
                reason = "主站返回空表"
            else:
                seen = _session_volume(chunk)
                if abs(seen - want) <= _VOLUME_TOLERANCE:
                    rows = chunk
                    break
                reason = f"成交量不符（逐笔 {seen:.0f} 手 / 日K {want:.0f} 手）"
            ep.drop()
            time.sleep(0.4 * attempt)
        if rows is None:
            raise IncompleteDayError(f"{code} {day} 连续 {_DAY_ATTEMPTS} 次失败：{reason}")
        if rows:
            out[day] = rows
    return out


def _direction(row: dict) -> int:
    # 不能写 `int(row.get("buyorsell") or 2)`：主动买就是 0，会被 or 当成假值。
    raw = row.get("buyorsell")
    return 2 if raw is None else int(raw)


def run_job(job: Job, ep: Endpoint) -> tuple[pl.DataFrame, pl.DataFrame]:
    """取一只股票一年的逐笔，返回 (分钟增强表, 逐笔原层)。"""
    ticks = fetch_ticks(ep, job.market, job.code, job.days)

    bar_frames: list[pl.DataFrame] = []
    tick_dates: list[date] = []
    tick_times: list[str] = []
    tick_prices: list[float] = []
    tick_vols: list[float] = []
    tick_dirs: list[int] = []
    for day in sorted(ticks):
        items = ticks[day]
        bars = _ticks_to_bars(items, job.symbol, day, with_direction=True)
        if bars is not None and not bars.is_empty():
            bar_frames.append(bars)
        for row in items:
            tick_dates.append(day)
            tick_times.append(str(row.get("time") or ""))
            tick_prices.append(float(row.get("price") or 0.0))
            tick_vols.append(float(row.get("vol") or 0.0))
            tick_dirs.append(_direction(row))

    bars_frame = pl.DataFrame()
    if bar_frames:
        bars_frame = pl.concat(bar_frames, how="diagonal_relaxed").rename({"dt": "datetime"}).select(
            pl.col("symbol"),
            pl.col("datetime").cast(pl.Datetime("us")),
            pl.col("datetime").dt.date().alias("date"),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64),
            pl.col("amount").cast(pl.Float64),
            pl.col("buy_volume").cast(pl.Float64),
            pl.col("sell_volume").cast(pl.Float64),
        ).sort("datetime")

    ticks_frame = pl.DataFrame(
        {
            "symbol": [job.symbol] * len(tick_dates),
            "date": tick_dates,
            "time": tick_times,
            "price": tick_prices,
            "volume": tick_vols,
            "direction": tick_dirs,
        },
        schema_overrides={"date": pl.Date, "price": pl.Float64, "volume": pl.Float64,
                          "direction": pl.Int8},
    )
    return bars_frame, ticks_frame


def _with_retry(fn, ep: Endpoint, *, attempts: int = 3):
    last: str | None = None
    for i in range(attempts):
        try:
            bars, ticks = fn()
            return bars, ticks, None
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            ep.drop()
            time.sleep(0.4 * (i + 1))
    return pl.DataFrame(), pl.DataFrame(), last


# --------------------------------------------------------------------------- #
# 年度日历与分片规划
# --------------------------------------------------------------------------- #
def load_year_calendar(calendar_dir: Path, year: int) -> dict[str, list[tuple[date, float]]]:
    """从日K 备份里取「这一年哪些股票成交过、在哪几天、当天成交多少手」。

    用日K 当权威日历，可以自然排除停牌股与当年还没上市/已退市的股票，不用猜。
    **同时带回当天成交量**，抓取时用它校验逐笔是否取全——只看「非空」不够，
    页尾被截断同样是静默丢数据。

    2000 年必须夹到 ``TICKS_FLOOR``（06-09）：日K 从 01-04 就有，但逐笔数据 06-09
    才开始，不夹的话 73,722 个股票日（占 2000 年 39%）全是必然为空的请求。
    """
    path = calendar_dir / f"{year}.csv.gz"
    if not path.exists():
        return {}
    frame = pl.read_csv(path, columns=["symbol", "date", "volume"], try_parse_dates=True)
    if frame.is_empty():
        return {}
    frame = frame.filter(pl.col("date") >= TICKS_FLOOR)
    if frame.is_empty():
        return {}
    out: dict[str, list[tuple[date, float]]] = {}
    ordered = frame.sort(["symbol", "date"]).select(["symbol", "date", "volume"])
    for symbol, day, vol in ordered.iter_rows():
        out.setdefault(symbol, []).append((day, float(vol)))
    return out


def est_ticks_per_day(year: int) -> int:
    """每天每只股票大约多少笔（3 秒快照的上限是 4800，冷门股有跳空）。"""
    if year <= 2005:
        return 1400
    if year <= 2010:
        return 2200
    if year <= 2015:
        return 3200
    return 4600


def build_shards(symbols: Sequence[str], calendar: dict[str, list[tuple[date, float]]],
                 year: int, shard_bytes: int) -> list[list[str]]:
    """把股票切成「整只对齐」的分片，按估计原始字节累加到 shard_bytes 封口。

    确定性：同样的输入永远切出同样的分片，所以重跑时同一批股票还是同一个 part 名。
    """
    per_day = est_ticks_per_day(year) * BYTES_PER_TICK_RAW
    shards: list[list[str]] = []
    current: list[str] = []
    size = 0
    for symbol in symbols:
        cost = len(calendar[symbol]) * per_day
        if current and size + cost > shard_bytes:
            shards.append(current)
            current, size = [], 0
        current.append(symbol)
        size += cost
    if current:
        shards.append(current)
    return shards


# --------------------------------------------------------------------------- #
# 中转库
# --------------------------------------------------------------------------- #
def open_store(path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path))
    con.execute("PRAGMA threads=2")
    con.execute("PRAGMA memory_limit='6GB'")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {MINUTE_TABLE}(
            symbol VARCHAR, datetime TIMESTAMP, date DATE,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE, buy_volume DOUBLE, sell_volume DOUBLE
        )
        """
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS shard_done("
        "shard INTEGER, symbols INTEGER, bars BIGINT, ticks BIGINT, "
        "file VARCHAR, bytes BIGINT, finished_at TIMESTAMP)"
    )
    return con


def _pick_root(roots: Sequence[Path], need_bytes: int) -> Path:
    """在多个目标盘里挑一个放得下这一年的。"""
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(root).free
        if free > need_bytes:
            return root
    detail = ", ".join(f"{r}({shutil.disk_usage(r).free / 1e9:.0f}GB)" for r in roots)
    raise RuntimeError(f"没有盘放得下这一年（需要 {need_bytes / 1e9:.0f}GB）：{detail}")


# --------------------------------------------------------------------------- #
# 单分片处理
# --------------------------------------------------------------------------- #
def run_shard(
    con, shard_idx: int, symbols: Sequence[str], days_map: dict[str, list[tuple[date, float]]],
    ep: Endpoint, *, workers: int, ticks_dir: Path,
) -> dict[str, Any]:
    jobs: list[Job] = []
    for symbol in symbols:
        parsed = qc.split_symbol(symbol)
        if parsed is None:
            continue
        days = tuple((day, int(day.strftime("%Y%m%d")), vol) for day, vol in days_map[symbol])
        jobs.append(Job(symbol, parsed[0], parsed[1], days))

    results: dict[str, tuple[pl.DataFrame, pl.DataFrame]] = {}
    failed: list[dict[str, str]] = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for offset in range(0, len(jobs), workers):
            batch = jobs[offset:offset + workers]
            futures = {pool.submit(_with_retry, lambda j=job: run_job(j, ep), ep): job for job in batch}
            for future in as_completed(futures):
                job = futures[future]
                bars, ticks, error = future.result()
                if error:
                    failed.append({"symbol": job.symbol, "error": error})
                else:
                    results[job.symbol] = (bars, ticks)

    tick_frames = [results[s][1] for s in symbols if s in results]
    ticks_frame = (
        pl.concat(tick_frames, how="vertical") if tick_frames
        else pl.DataFrame(schema={"symbol": pl.String, "date": pl.Date, "time": pl.String,
                                  "price": pl.Float64, "volume": pl.Float64,
                                  "direction": pl.Int8})
    )
    ticks_dir.mkdir(parents=True, exist_ok=True)
    target = ticks_dir / f"part-{shard_idx:04d}.csv.gz"
    # polars 的 csv 写入会校验后缀, 临时文件也必须以 .csv.gz 结尾。
    tmp = ticks_dir / f".part-{shard_idx:04d}.tmp.csv.gz"
    if tmp.exists():
        tmp.unlink()
    ticks_frame.write_csv(tmp, compression="gzip")
    tmp.replace(target)

    bar_frames = [results[s][0] for s in symbols if s in results and not results[s][0].is_empty()]
    if bar_frames:
        frame = pl.concat(bar_frames, how="vertical")
        con.executemany("DELETE FROM " + MINUTE_TABLE + " WHERE symbol = ?",
                        [[s] for s in symbols])
        con.register("_incoming", frame.to_arrow())
        con.execute(
            f"INSERT INTO {MINUTE_TABLE} SELECT symbol, datetime, date, open, high, low, close, "
            f"volume, amount, buy_volume, sell_volume FROM _incoming"
        )
        con.unregister("_incoming")
        bars = frame.height
    else:
        bars = 0

    stats = {
        "shard": shard_idx,
        "symbols": len(results),
        "bars": bars,
        "ticks": ticks_frame.height,
        "file": target.name,
        "bytes": target.stat().st_size,
        "seconds": round(time.time() - started, 1),
        "failed": failed,
    }
    if not failed:
        con.execute(
            "INSERT INTO shard_done VALUES (?, ?, ?, ?, ?, ?, ?)",
            [shard_idx, stats["symbols"], bars, stats["ticks"], target.name,
             stats["bytes"], datetime.now()],
        )
    return stats


# --------------------------------------------------------------------------- #
# 年度导出：一个 zip，内含「一只股票一个 csv」
# --------------------------------------------------------------------------- #
def _csv_bytes(frame: pl.DataFrame) -> bytes:
    return frame.write_csv().encode("utf-8")


def export_minute_zip(
    con, out_root: Path, year: int, symbols: Sequence[str], *, batch: int = 100,
) -> dict:
    """把该年分钟增强表按「一只股票一个 csv」写进 ``<year>.zip``。

    分批查 symbol 而不是一次全表拉进内存：一只热门股一年约 5.8 万行，100 只一批
    约 600 MB，远低于内存上限，也不用先在磁盘上落 parquet 中转。
    """
    target = out_root / "minute" / MINUTE_TABLE / f"{year}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{year}.tmp.zip")
    if tmp.exists():
        tmp.unlink()

    rows = con.execute(f"SELECT count(*) FROM {MINUTE_TABLE}").fetchone()[0]
    if not rows:
        return {"year": year, "rows": 0, "symbols": 0, "file": None, "bytes": 0}

    written = total = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=ZIP_COMPRESSLEVEL, allowZip64=True) as zf:
        for offset in range(0, len(symbols), batch):
            chunk = list(symbols[offset:offset + batch])
            marks = ",".join("?" * len(chunk))
            frame = con.execute(
                f"SELECT symbol, datetime, date, open, high, low, close, volume, amount, "
                f"buy_volume, sell_volume FROM {MINUTE_TABLE} "
                f"WHERE symbol IN ({marks}) ORDER BY symbol, datetime",
                chunk,
            ).pl()
            if frame.is_empty():
                continue
            for key, part in sorted(frame.partition_by("symbol", as_dict=True).items()):
                # 分钟 K 没有亚秒精度，写成 ``YYYY-MM-DD HH:MM:SS``：比 ISO 的
                # ``...T09:31:00.000000`` 每行省 7 字节，Excel 与 read_csv 都能直读。
                out = part.with_columns(
                    pl.col("datetime").dt.strftime("%Y-%m-%d %H:%M:%S")
                )
                zf.writestr(f"{key[0]}.csv", _csv_bytes(out))
                written += 1
                total += part.height
            del frame
    tmp.replace(target)
    return {"year": year, "rows": total, "symbols": written,
            "file": str(target), "bytes": target.stat().st_size}


def export_ticks_zip(ticks_dir: Path, target: Path) -> dict:
    """把该年逐笔分片按「一只股票一个 csv」打包成 ``<year>_ticks.zip``。

    分片本身就按「整只股票」切分，且分片顺序与片内股票顺序都是代码序，所以直接
    顺序读分片、逐个成员写入，不需要全局排序，也不需要落 parquet 中转。
    """
    shards = sorted(ticks_dir.glob("part-*.csv.gz"))
    if not shards:
        return {"rows": 0, "symbols": 0, "shards": 0, "file": None, "bytes": 0}

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    if tmp.exists():
        tmp.unlink()

    written = total = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=ZIP_COMPRESSLEVEL, allowZip64=True) as zf:
        for path in shards:
            # 不能用 try_parse_dates：它会把 time 推断成 Time 类型，写回时补成
            # ``09:25:00.000000000``，每行多 10 字节（全量约 57 GB）。
            frame = pl.read_csv(path, schema_overrides={
                "symbol": pl.String, "date": pl.String, "time": pl.String,
                "price": pl.Float64, "volume": pl.Float64, "direction": pl.Int8,
            })
            for key, part in sorted(frame.partition_by("symbol", as_dict=True).items()):
                zf.writestr(f"{key[0]}.csv", _csv_bytes(part))
                written += 1
                total += part.height
            del frame
    tmp.replace(target)
    return {"rows": total, "symbols": written, "shards": len(shards),
            "file": str(target), "bytes": target.stat().st_size}


def purge_tree(path: Path) -> None:
    """删一棵目录树。

    不用 ``shutil.rmtree``：本机 python 启动时挂的 sitecustomize 在批量删除超过 50 个
    文件时会直接 ``SystemExit(1)``，会把整个进程带崩。
    """
    if not path.exists():
        return
    files = [p for p in path.rglob("*") if p.is_file()]
    for item in files:
        item.unlink(missing_ok=True)
    dirs = sorted((p for p in path.rglob("*") if p.is_dir()),
                  key=lambda p: len(p.parts), reverse=True)
    for item in dirs:
        item.rmdir()
    path.rmdir()


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按年导出历史分钟数据（分钟增强表 + 逐笔原层）")
    parser.add_argument("--out", type=Path, default=Path("D:/tsp/backup_export"),
                        help="分钟表与 manifest 的输出目录")
    parser.add_argument("--ticks-roots", default="E:/tsp_backup_ticks,D:/tsp_backup_ticks",
                        help="逐笔原层放在哪些盘（逗号分隔，按剩余空间自动挑）")
    parser.add_argument("--calendar", type=Path, default=None, help="日K 备份目录（提供交易日与在市股票）")
    parser.add_argument("--years", default="", help="逗号分隔的年份；留空配合 --from-year/--to-year")
    parser.add_argument("--from-year", type=int, default=2000)
    parser.add_argument("--to-year", type=int, default=date.today().year)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--endpoints", type=int, default=8)
    parser.add_argument("--shard-mb", type=int, default=512, help="单个逐笔分片的目标原始体积")
    parser.add_argument("--max-symbols", type=int, default=0, help="调试：每年最多几只")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不连网不写盘")
    parser.add_argument("--keep-store", action="store_true", help="导出后保留该年中转库")
    args = parser.parse_args(argv)

    out_root = args.out.resolve()
    calendar_dir = (args.calendar or (out_root / "daily" / "kline_daily")).resolve()
    if not calendar_dir.exists():
        print(f"日历目录不存在: {calendar_dir}（先跑 export_history_backup.py 出日K）")
        return 2

    if args.years.strip():
        years = sorted({int(y) for y in args.years.split(",") if y.strip()})
    else:
        years = list(range(args.from_year, args.to_year + 1))

    work = out_root / "_staging_minute"
    work.mkdir(parents=True, exist_ok=True)
    marker_dir = out_root / "minute" / MINUTE_TABLE
    marker_dir.mkdir(parents=True, exist_ok=True)
    ticks_roots = [Path(p.strip()) for p in args.ticks_roots.split(",") if p.strip()]

    calendars: dict[int, dict[str, list[tuple[date, float]]]] = {}
    plan: dict[int, dict[str, Any]] = {}
    for year in years:
        if year < TICKS_SINCE_YEAR:
            print(f"{year}: 早于逐笔数据下限（{TICKS_SINCE_YEAR}-06-09），跳过", flush=True)
            continue
        calendar = load_year_calendar(calendar_dir, year)
        if not calendar:
            print(f"{year}: 无日K日历，跳过", flush=True)
            continue
        symbols = sorted(calendar)
        if args.max_symbols:
            symbols = symbols[: args.max_symbols]
        calendars[year] = calendar
        shards = build_shards(symbols, calendar, year, args.shard_mb * 1024 * 1024)
        stock_days = sum(len(calendar[s]) for s in symbols)
        est_ticks = stock_days * est_ticks_per_day(year)
        plan[year] = {
            "symbols": len(symbols),
            "stock_days": stock_days,
            "shards": len(shards),
            "est_ticks": est_ticks,
            "est_gz_gb": est_ticks * BYTES_PER_TICK_GZ / 1e9,
            "est_hours": stock_days * 0.11 / max(args.workers, 1) / 3600,
        }

    if not plan:
        print("没有可跑的年份")
        return 2

    print("年份   股票    股·天      分片   估计笔数     逐笔gz    估计耗时")
    for year, p in plan.items():
        print(f"{year}  {p['symbols']:>5,} {p['stock_days']:>10,} {p['shards']:>6,} "
              f"{p['est_ticks'] / 1e8:>8.1f}亿 {p['est_gz_gb']:>8.1f}GB {p['est_hours']:>7.1f}h")
    total_ticks = sum(p["est_ticks"] for p in plan.values())
    total_hours = sum(p["est_hours"] for p in plan.values())
    print(f"合计  {sum(p['symbols'] for p in plan.values()):>5,} "
          f"{sum(p['stock_days'] for p in plan.values()):>10,} "
          f"{sum(p['shards'] for p in plan.values()):>6,} "
          f"{total_ticks / 1e8:>8.1f}亿 "
          f"{sum(p['est_gz_gb'] for p in plan.values()):>8.1f}GB {total_hours:>7.1f}h")
    if args.dry_run:
        print("\n--dry-run：未连网、未写盘。")
        return 0

    manifest_path = out_root / "minute_manifest.json"
    manifest: dict[str, Any] = {"years": {}}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            manifest = {"years": {}}

    overall = time.time()
    for year, p in plan.items():
        marker = marker_dir / f".year-{year}.done"
        if marker.exists():
            print(f"\n== {year} 已完成，跳过 ==", flush=True)
            continue

        calendar = calendars[year]
        symbols = sorted(calendar)
        if args.max_symbols:
            symbols = symbols[: args.max_symbols]
        shards = build_shards(symbols, calendar, year, args.shard_mb * 1024 * 1024)
        need = p["est_gz_gb"] * 1e9 * 1.4 + 20e9  # 留 40% 余量与 20GB 缓冲
        ticks_root = _pick_root(ticks_roots, need)
        ticks_dir = ticks_root / str(year)
        print(f"\n== {year}: {len(symbols)} 只 / {p['stock_days']:,} 股·天 / {len(shards)} 片 "
              f"→ {ticks_root}（余 {shutil.disk_usage(ticks_root).free / 1e9:.0f}GB）==", flush=True)

        targets = probe_days(calendar)
        endpoints = pick_endpoints(args.endpoints, probe_ymds=targets)
        ep = Endpoint(endpoints)
        print(f"  主站 {len(endpoints)} 台（已验证 {'/'.join(map(str, targets))} 有逐笔）", flush=True)

        con = open_store(work / f"minute-{year}.duckdb")
        done = {row[0] for row in con.execute("SELECT shard FROM shard_done").fetchall()}
        year_bars = year_ticks = 0
        failures: list[dict[str, Any]] = []
        for idx, shard in enumerate(shards):
            if idx in done:
                continue
            stats = run_shard(con, idx, shard, calendar, ep,
                              workers=args.workers, ticks_dir=ticks_dir)
            year_bars += stats["bars"]
            year_ticks += stats["ticks"]
            if stats["failed"]:
                failures += stats["failed"]
                print(f"  片 {idx:04d}: {stats['symbols']} 只 失败 {len(stats['failed'])} 只"
                      f"（重跑会重试）", flush=True)
            elif idx % 10 == 0 or idx == len(shards) - 1:
                elapsed = time.time() - overall
                print(f"  片 {idx + 1}/{len(shards)} 累计 {year_bars:,} 行分钟 / "
                      f"{year_ticks / 1e6:.1f}M 笔逐笔 / 已跑 {elapsed / 3600:.1f}h", flush=True)

        print("  打包分钟表…", flush=True)
        minute_summary = export_minute_zip(con, out_root, year, symbols)
        con.close()

        ticks_target = ticks_root / f"{year}_ticks.zip"
        print(f"  打包逐笔…（{len(list(ticks_dir.glob('part-*.csv.gz')))} 片）", flush=True)
        ticks_summary = export_ticks_zip(ticks_dir, ticks_target)

        summary = {
            "year": year,
            "minute_rows": minute_summary["rows"],
            "minute_symbols": minute_summary["symbols"],
            "minute_zip": minute_summary["file"],
            "minute_bytes": minute_summary["bytes"],
            "ticks_rows": ticks_summary["rows"],
            "ticks_symbols": ticks_summary["symbols"],
            "ticks_zip": ticks_summary["file"],
            "ticks_bytes": ticks_summary["bytes"],
            "ticks_root": str(ticks_root),
            "failed": len(failures),
        }
        print(f"  分钟 {minute_summary['file']}: {minute_summary['rows']:,} 行 / "
              f"{minute_summary['symbols']} 只 / {minute_summary['bytes'] / 1e9:.2f} GB", flush=True)
        print(f"  逐笔 {ticks_summary['file']}: {ticks_summary['rows']:,} 笔 / "
              f"{ticks_summary['symbols']} 只 / {ticks_summary['bytes'] / 1e9:.2f} GB", flush=True)

        # 打包成功才清中间件：有失败分片时保留分片与中转库，重跑同一条命令即可补。
        if not failures and not args.keep_store:
            purge_tree(ticks_dir)
            for leftover in work.glob(f"minute-{year}.duckdb*"):
                leftover.unlink(missing_ok=True)
            print("  已清理分片与年度中转库", flush=True)

        # 小样本试跑（--max-symbols）不能写年完成标记，否则全量跑会被整年跳过。
        if not failures and not args.max_symbols:
            marker.write_text(
                json.dumps({"year": year, "minute_rows": summary["minute_rows"],
                            "ticks_rows": summary["ticks_rows"],
                            "at": datetime.now().isoformat()}, ensure_ascii=False),
                encoding="utf-8",
            )
        manifest["years"][str(year)] = summary
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n全部完成，总用时 {(time.time() - overall) / 3600:.1f} 小时 → {out_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
