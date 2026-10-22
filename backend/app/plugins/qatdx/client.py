"""pytdx 连接与翻页。行为对齐 QUANTAXIS QATdx，但不导入 QUANTAXIS。

每次调用新建连接并串行加锁：pytdx 会话不能并发。主站池是 pytdx
自带 hq_hosts，再附上 QA 仍在用、但不在那张表里的地址。先并发探测 TCP，
再按延迟做协议握手，选第一个证券数量大于 1000 的地址，进程内缓存一小时。
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 2022-11 以后 QA 仍在用、且不在 pytdx hq_hosts 里的主站。放在池尾。
_EXTRA_SERVERS: tuple[tuple[str, int], ...] = (
    ("124.71.187.122", 7709),
    ("119.97.185.59", 7709),
    ("47.107.64.168", 7709),
    ("124.70.75.113", 7709),
    ("124.71.9.153", 7709),
    ("123.60.84.66", 7709),
    ("120.46.186.223", 7709),
    ("116.205.163.254", 7709),
)

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"ip": None, "port": None, "at": 0.0}
_CACHE_TTL_S = 3600
_PAGE = 800
_QUOTE_BATCH = 80
_MAX_BARS = 20800

# pytdx K 线类别，与 QATdx._select_type / 分钟分支一致。
_BAR_CATEGORY = {
    "1m": 8,
    "5m": 0,
    "15m": 1,
    "30m": 2,
    "60m": 3,
    "1d": 9,
    "1w": 5,
    "1M": 6,
}


def bar_category(freq: str) -> int | None:
    key = str(freq or "1d").lower()
    aliases = {
        "day": "1d", "d": "1d", "1day": "1d",
        "1min": "1m", "min_1": "1m",
        "5min": "5m", "min_5": "5m",
        "15min": "15m", "min_15": "15m",
        "30min": "30m", "min_30": "30m",
        "60min": "60m", "min_60": "60m", "1h": "60m",
        "week": "1w", "w": "1w",
        "month": "1M", "m": "1M",
    }
    key = aliases.get(key, key)
    return _BAR_CATEGORY.get(key)


def split_symbol(symbol: str, *, index: bool = False) -> tuple[int, str] | None:
    """面板代码 → (pytdx 市场号, 6 位代码)。无法识别返回 None，不猜测交易所。

    沪=1，深=0，北交所=2。指数 399xxx 在深市，其余指数代码走沪市，
    与 QATdx._select_index_code 一致。
    """
    raw = str(symbol or "").strip().upper()
    if "." not in raw:
        return None
    code, _, exch = raw.partition(".")
    if len(code) != 6 or not code.isdigit() or exch not in {"SH", "SZ", "BJ"}:
        return None
    if exch == "BJ":
        return 2, code
    if index:
        return (0, code) if code.startswith("3") else (1, code)
    if exch == "SH":
        return 1, code
    return 0, code


def to_symbol(market: int, code: str) -> str | None:
    code = str(code or "").strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        return None
    if market == 1:
        return f"{code}.SH"
    if market == 0:
        return f"{code}.SZ"
    if market == 2:
        return f"{code}.BJ"
    return None


def _vendor_root() -> Path:
    """插件内的 yutiansut/pytdx 源码根目录（里面才是 pytdx 包）。"""
    return Path(__file__).resolve().parent / "vendor" / "pytdx"


def _ensure_vendor_path() -> None:
    """把源码目录放到 sys.path 最前，避免用到环境里另装的 pytdx。"""
    root = str(_vendor_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _import_api():
    _ensure_vendor_path()
    from pytdx.hq import TdxHq_API
    return TdxHq_API


def _server_pool() -> list[tuple[str, int]]:
    """pytdx hq_hosts 加上 QA 额外主站，按 (ip, port) 去重。"""
    _ensure_vendor_path()
    from pytdx.config.hosts import hq_hosts

    pool: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for item in list(hq_hosts) + [(None, ip, port) for ip, port in _EXTRA_SERVERS]:
        ip, port = str(item[1]).strip(), int(item[2])
        key = (ip, port)
        if not ip or key in seen:
            continue
        seen.add(key)
        pool.append(key)
    return pool


def _tcp_latency_ms(ip: str, port: int, timeout: float) -> float | None:
    """只测 TCP 能否连上，返回毫秒延迟。失败返回 None。"""
    import socket

    sock = socket.socket()
    sock.settimeout(timeout)
    started = time.perf_counter()
    try:
        sock.connect((ip, int(port)))
    except Exception:
        return None
    else:
        return (time.perf_counter() - started) * 1000
    finally:
        sock.close()


def _select_server() -> tuple[str, int]:
    now = time.time()
    if _CACHE["ip"] and now - float(_CACHE["at"]) < _CACHE_TTL_S:
        return _CACHE["ip"], int(_CACHE["port"])
    pool = _server_pool()
    from concurrent.futures import ThreadPoolExecutor

    latency: dict[tuple[str, int], float] = {}
    with ThreadPoolExecutor(max_workers=16) as pool_exec:
        futures = {
            pool_exec.submit(_tcp_latency_ms, ip, port, 1.5): (ip, port)
            for ip, port in pool
        }
        for future in futures:
            ip_port = futures[future]
            try:
                delay = future.result()
            except Exception:
                delay = None
            if delay is not None:
                latency[ip_port] = delay
    ordered = sorted(latency, key=lambda item: latency[item]) or list(pool)
    api_cls = _import_api()
    last_error: Exception | None = None
    for ip, port in ordered:
        api = api_cls(raise_exception=True)
        try:
            with api.connect(ip, port, time_out=2.0):
                count = api.get_security_count(0)
            if count and int(count) > 1000:
                _CACHE.update(ip=ip, port=port, at=now)
                logger.info(
                    "qatdx 选用行情主站 %s:%s (池 %d, TCP 通 %d)",
                    ip, port, len(pool), len(latency),
                )
                return ip, port
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
    raise RuntimeError(f"QATdx 行情主站均不可用: {last_error}")


def _call(fn: Callable[..., Any]) -> Any:
    with _LOCK:
        ip, port = _select_server()
        api = _import_api()(raise_exception=True)
        with api.connect(ip, port, time_out=2.0):
            return fn(api)


def stock_bars(market: int, code: str, category: int, count: int) -> list[dict]:
    """从最近一根往回取 count 根，800 一页。成交量保持 pytdx 的手。"""
    count = max(1, min(int(count), _MAX_BARS))
    pages = (count + _PAGE - 1) // _PAGE

    def _run(api):
        rows: list[dict] = []
        for i in range(pages):
            chunk = api.get_security_bars(category, market, code, i * _PAGE, _PAGE) or []
            rows.extend(chunk)
            if len(chunk) < _PAGE:
                break
        return rows

    return list(_call(_run))[:count]


def quotes(pairs: list[tuple[int, str]]) -> list[dict]:
    if not pairs:
        return []

    def _run(api):
        rows: list[dict] = []
        for i in range(0, len(pairs), _QUOTE_BATCH):
            batch = pairs[i:i + _QUOTE_BATCH]
            rows.extend(api.get_security_quotes(batch) or [])
        return rows

    return list(_call(_run))


def xdxr(market: int, code: str) -> list[dict]:
    def _run(api):
        return list(api.get_xdxr_info(market, code) or [])

    return _call(_run)


def finance(market: int, code: str) -> dict:
    def _run(api):
        data = api.get_finance_info(market, code)
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data:
            first = data[0]
            return first if isinstance(first, dict) else {}
        return {}

    return _call(_run)


def security_list(market: int) -> list[dict]:
    def _run(api):
        total = int(api.get_security_count(market) or 0)
        rows: list[dict] = []
        start = 0
        while start < total:
            chunk = api.get_security_list(market, start) or []
            if not chunk:
                break
            rows.extend(chunk)
            start += 1000
        return rows

    return _call(_run)


def transactions(market: int, code: str, trade_day: date) -> list[dict]:
    """历史分笔。pytdx 单次约 2000 条，最多翻 20 页。"""
    ymd = int(trade_day.strftime("%Y%m%d"))

    def _run(api):
        rows: list[dict] = []
        for page in range(20):
            # 这份 pytdx 的签名是 (market, code, start, count, date)，不是旧的 (date, start)。
            chunk = api.get_history_transaction_data(
                market, code, page * 2000, 2000, ymd,
            ) or []
            if not chunk:
                break
            rows.extend(chunk)
            if len(chunk) < 2000:
                break
        return rows

    return _call(_run)


def blocks() -> list[dict]:
    """通达信板块文件。失败的文件跳过，不把空文件当成全部失败。"""
    names = (
        ("block_gn.dat", "concept"),
        ("block_fg.dat", "style"),
        ("block.dat", "region"),
        ("block_zs.dat", "index"),
    )

    def _run(api):
        rows: list[dict] = []
        for filename, kind in names:
            try:
                parsed = api.get_and_parse_block_info(filename) or []
            except Exception as exc:  # noqa: BLE001
                logger.warning("qatdx 板块 %s 读取失败: %s", filename, exc)
                continue
            for item in parsed:
                if isinstance(item, dict):
                    row = dict(item)
                    row["block_type"] = kind
                    rows.append(row)
        return rows

    return _call(_run)


def industries() -> list[dict]:
    """行业对照。优先走行情连接里的 tdxhy.cfg；没有就返回空。"""
    def _run(api):
        getter = getattr(api, "get_block_dat_ver_up", None)
        if getter is None:
            return []
        try:
            raw = getter("tdxhy.cfg")
        except Exception as exc:  # noqa: BLE001
            logger.warning("qatdx 行业文件读取失败: %s", exc)
            return []
        text = raw.decode("gb18030", errors="replace") if isinstance(raw, bytes) else str(raw or "")
        return [{"line": line} for line in text.splitlines() if line and not line.startswith("#")]

    return _call(_run)


def margin(trade_day: date) -> list[dict]:
    """沪深融资融券。网页接口失败时返回空列表，不换数据源。"""
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    day = trade_day.strftime("%Y%m%d")
    iso = trade_day.isoformat()
    headers = {"User-Agent": "Mozilla/5.0"}
    rows: list[dict] = []
    sh_url = f"http://www.sse.com.cn/market/dealingdata/overview/margin/a/rzrqjygk{day}.xls"
    try:
        req = Request(sh_url, headers=headers)
        with urlopen(req, timeout=8) as resp:
            payload = resp.read()
        rows.extend(_parse_sh_margin(payload, iso))
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning("qatdx 上交所融资融券失败: %s", exc)
    sz_url = (
        "http://www.szse.cn/api/report/ShowReport.szse?SHOWTYPE=xlsx"
        f"&CATALOGID=1837_xxpl&txtDate={iso}&tab2PAGENO=1&ENCODE=1&TABKEY=tab2"
    )
    try:
        req = Request(sz_url, headers=headers)
        with urlopen(req, timeout=8) as resp:
            payload = resp.read()
        rows.extend(_parse_sz_margin(payload, iso))
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        logger.warning("qatdx 深交所融资融券失败: %s", exc)
    return rows


def _parse_sh_margin(payload: bytes, iso: str) -> list[dict]:
    return _parse_margin_table(payload, iso, "SH")


def _parse_sz_margin(payload: bytes, iso: str) -> list[dict]:
    return _parse_margin_table(payload, iso, "SZ")


def _parse_margin_table(payload: bytes, iso: str, exchange: str) -> list[dict]:
    """尽量用标准库读 csv/tsv；xls/xlsx 交给 polars，失败则空。"""
    text = ""
    for encoding in ("utf-8", "gb18030"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text and ("," in text or "\t" in text) and "证券代码" in text:
        return _rows_from_delimited(text, iso, exchange)
    try:
        import polars as pl
        frame = pl.read_excel(payload)
    except Exception:  # noqa: BLE001
        return []
    return _rows_from_frame(frame, iso, exchange)


def _rows_from_delimited(text: str, iso: str, exchange: str) -> list[dict]:
    import csv
    sample = text.splitlines()[0] if text else ""
    delimiter = "\t" if sample.count("\t") >= sample.count(",") else ","
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    rows = []
    for item in reader:
        code = str(item.get("证券代码") or item.get("标的证券代码") or "").strip().zfill(6)
        if not code.isdigit():
            continue
        symbol = f"{code}.{exchange}"
        rows.append({
            "symbol": symbol,
            "trade_date": iso,
            "leveraged_balance": _num(item.get("本日融资余额(元)") or item.get("融资余额(元)")),
            "leveraged_buyout": _num(item.get("本日融资买入额(元)") or item.get("融资买入额(元)")),
            "margin_sell": _num(item.get("本日融券卖出量(股/份)") or item.get("融券卖出量(股/份)")),
            "margin_left": _num(item.get("本日融券余量(股/份)") or item.get("融券余量(股/份)")),
            "exchange": exchange,
        })
    return rows


def _rows_from_frame(frame, iso: str, exchange: str) -> list[dict]:
    columns = set(frame.columns)
    code_col = "证券代码" if "证券代码" in columns else "标的证券代码" if "标的证券代码" in columns else None
    if code_col is None:
        return []
    rows = []
    for item in frame.iter_rows(named=True):
        code = str(item.get(code_col) or "").strip().zfill(6)
        if not code.isdigit():
            continue
        rows.append({
            "symbol": f"{code}.{exchange}",
            "trade_date": iso,
            "leveraged_balance": _num(item.get("本日融资余额(元)") or item.get("融资余额(元)")),
            "leveraged_buyout": _num(item.get("本日融资买入额(元)") or item.get("融资买入额(元)")),
            "margin_sell": _num(item.get("本日融券卖出量(股/份)") or item.get("融券卖出量(股/份)")),
            "margin_left": _num(item.get("本日融券余量(股/份)") or item.get("融券余量(股/份)")),
            "exchange": exchange,
        })
    return rows


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def reset_server_cache() -> None:
    """测试用。"""
    _CACHE.update(ip=None, port=None, at=0.0)
