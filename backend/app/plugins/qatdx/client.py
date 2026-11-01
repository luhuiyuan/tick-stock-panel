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
from collections.abc import Callable, Sequence
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
_CACHE: dict[str, Any] = {
    "ip": None, "port": None, "at": 0.0,
    "history_ip": None, "history_port": None, "history_at": 0.0,
    # 已实测能查到的最早日期(ymd)。历史主站覆盖深度不同, 缓存必须带上这个下限,
    # 否则「先查近期再查远期」时会误用只能查近期的免费主站。
    "history_from": None,
}
_CACHE_TTL_S = 3600
_PAGE = 800
_QUOTE_BATCH = 80
_MAX_BARS = 20800
# 协议握手阶段每批并行探测的主站数。串行试完 40 台最坏要 80s, 冷启动实测 9.1s;
# 并行后通常第一批就命中。
_PROBE_BATCH = 6
_TICK_PAGE = 1800
_TICK_MAX_PAGES = 40
# 探测主站是否存历史数据用的默认日期(2024-01-02, 周二)。实际调用会传入本次
# 请求的最早日期, 只有调用点确实不知道日期时才用这个值。
_HISTORY_PROBE = 20240102
# 协议层直接拒绝的市场号(主站不提供该市场)。进程内记住, 不再重复请求。
_UNSUPPORTED_MARKETS: set[int] = set()

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


def _supports_history(api, probe_ymd: int | None = None) -> bool:
    """主站是否真的存**目标日期**的历史数据。

    不是每台主站都有, 而且覆盖深度不同: 实测 119.97.185.59 有 2024-01-02 的
    分时, 但 2015/2008 一律返回空; 218.106.92.182 / 180.153.18.170 则能回溯
    到 2008 年。用固定探测点(旧实现写死 20240102)就会把前者判成「有历史」,
    选中后让远期查询静默返回空 —— 表现为同一个请求时而有时而无。

    所以探测点必须跟着本次请求的最早日期走。
    """
    ymd = int(probe_ymd or _HISTORY_PROBE)
    try:
        return bool(api.get_history_minute_time_data(0, "000001", ymd))
    except Exception:  # noqa: BLE001
        return False


def _select_server(*, need_history: bool = False, probe_ymd: int | None = None) -> tuple[str, int]:
    now = time.time()
    if need_history:
        cached_ip = _CACHE["history_ip"]
        covers = _CACHE["history_from"]
        # 缓存的这台必须「覆盖得足够早」: covers 是它实测能查到的最早日期,
        # 只有 covers <= 本次探测日 时才能确信本次请求不会拿到空表。
        if (cached_ip and now - float(_CACHE["history_at"]) < _CACHE_TTL_S
                and (probe_ymd is None or (covers and int(covers) <= int(probe_ymd)))):
            return str(cached_ip), int(_CACHE["history_port"])
    elif _CACHE["ip"] and now - float(_CACHE["at"]) < _CACHE_TTL_S:
        return _CACHE["ip"], int(_CACHE["port"])
    pool = _server_pool()
    from concurrent.futures import ThreadPoolExecutor

    latency: dict[tuple[str, int], float] = {}
    with ThreadPoolExecutor(max_workers=32) as pool_exec:
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

    def _probe(ip: str, port: int):
        """返回 ("ok"|"fallback", ip, port) 或 None。

        协议握手是一次 TCP 往返, 串行试 40 台最坏要 80s(旧实现冷启动实测 9.1s);
        并行按批探, 拿到第一个可用主站就返回。
        """
        api = api_cls(raise_exception=True)
        try:
            with api.connect(ip, port, time_out=2.0):
                count = api.get_security_count(0)
                if not (count and int(count) > 1000):
                    return None
                if need_history and not _supports_history(api, probe_ymd):
                    logger.debug("qatdx 主站 %s 无 %s 的历史分时/逐笔, 跳过", ip, probe_ymd)
                    return ("fallback", ip, port)
                return ("ok", ip, port)
        except Exception:  # noqa: BLE001
            return None

    fallback: tuple[str, int] | None = None
    last_error: Exception | None = None
    for offset in range(0, len(ordered), _PROBE_BATCH):
        batch = ordered[offset:offset + _PROBE_BATCH]
        with ThreadPoolExecutor(max_workers=len(batch)) as probe_exec:
            results = list(probe_exec.map(lambda pair: _probe(*pair), batch))
        for result in results:
            if result is None:
                continue
            kind, ip, port = result
            if kind == "ok":
                if need_history:
                    _CACHE.update(history_ip=ip, history_port=port, history_at=now,
                                  history_from=int(probe_ymd or _HISTORY_PROBE))
                else:
                    _CACHE.update(ip=ip, port=port, at=now)
                logger.info(
                    "qatdx 选用行情主站 %s:%s (池 %d, TCP 通 %d%s)",
                    ip, port, len(pool), len(latency),
                    f", 含 {probe_ymd} 历史" if need_history else "",
                )
                return ip, port
            if fallback is None:
                fallback = (ip, port)
                last_error = last_error or RuntimeError("历史探测未通过")
    if fallback is not None:
        # 探测日本身没有数据(节假日、探测股停牌)时, 所有主站都会被判「不合格」。
        # 这时不能整体失败, 用第一台能连上的主站去跑, 让调用方拿到空结果。
        #
        # 但这台**没通过** history 探测, 绝不能把 probe_ymd 记成它的覆盖下限:
        # 一旦写进 _CACHE["history_from"], 后续所有 probe_ymd' >= probe_ymd 的请求
        # 都会命中缓存复用它, 于是整段静默返回空表。实测 2015-01-01 是休市日,
        # 探测失败后 fallback 污染缓存, 导致 2015 全年逐笔/分时都是 0 行;
        # 重置缓存后同一批日期立刻拿到 5/5 天 23309 笔。
        ip, port = fallback
        logger.warning("qatdx 无主站通过 %s 历史探测, 临时用 %s:%s (不写入历史缓存)",
                       probe_ymd, ip, port)
        return ip, port
    raise RuntimeError(f"QATdx 行情主站均不可用: {last_error}")


def _call(fn: Callable[..., Any], *, need_history: bool = False,
          probe_ymd: int | None = None) -> Any:
    with _LOCK:
        ip, port = _select_server(need_history=need_history, probe_ymd=probe_ymd)
        api = _import_api()(raise_exception=True)
        with api.connect(ip, port, time_out=2.0):
            return fn(api)


# pytdx 的 get_volume 按通达信压缩浮点解包: 原始值高位字节为 0 时算出来的不是
# 真实数值, 而是 2^-127 (≈5.877e-39) 加几个可忽略项 —— 成交量为 0 的那一分钟
# 就会落成一根「0 < vol < 1」的假值, 出现在分时成交量和均价里。最小成交单位是
# 1 手, 小于 1 的正数不可能是真实量, 一律归 0。
_MIN_REAL_VOLUME = 1.0


def _clean_volumes(rows: list[dict]) -> None:
    for row in rows:
        for key in ("vol", "amount"):
            value = row.get(key)
            if isinstance(value, float) and 0.0 < value < _MIN_REAL_VOLUME:
                row[key] = 0.0


def _fetch_bars(market: int, code: str, category: int, count: int, *, index: bool) -> list[dict]:
    """取最近 count 根 K 线, 800 一页。成交量保持 pytdx 的手(分钟的量纲见 provider)。

    pytdx 的 ``start`` 是**从最新一根往回数**的偏移, 所以 start=0 的那一页是
    「最近 800 根」, 且页内按时间升序。早先这里直接 ``[:count]`` 截断, 拿到的
    是最新一页里**最旧**的 count 根(例如 count=30 会取到三年前的 30 根),
    调用方再按日期区间过滤就变成空表 —— 表现为"日K 同步成功但 0 行"。
    这里统一次序后取末尾 count 根, 多页时也成立。

    指数必须走 ``get_index_bars``: 指数回包的字段布局与股票不同(尾部多
    up_count/down_count), 用股票的 GetSecurityBarsCmd 解析出来是
    ``year=395942 / open=-76257`` 这类垃圾值, 落到 provider 侧便是
    "整段日期过滤后为空"。
    """
    count = max(1, min(int(count), _MAX_BARS))
    pages = (count + _PAGE - 1) // _PAGE

    def _run(api):
        fetch = api.get_index_bars if index else api.get_security_bars
        rows: list[dict] = []
        for i in range(pages):
            chunk = fetch(category, market, code, i * _PAGE, _PAGE) or []
            rows.extend(chunk)
            if len(chunk) < _PAGE:
                break
        return rows

    rows = list(_call(_run))
    rows.sort(key=lambda r: (
        int(r.get("year") or 0), int(r.get("month") or 0), int(r.get("day") or 0),
        int(r.get("hour") or 0), int(r.get("minute") or 0),
    ))
    _clean_volumes(rows)
    return rows[-count:]


def stock_bars(market: int, code: str, category: int, count: int) -> list[dict]:
    """证券(股票/ETF/债券)K 线。"""
    return _fetch_bars(market, code, category, count, index=False)


def index_bars(market: int, code: str, category: int, count: int) -> list[dict]:
    """指数 K 线。回包格式与股票不同, 必须用 get_index_bars。"""
    return _fetch_bars(market, code, category, count, index=True)


def history_minute_time(market: int, code: str, dates: Sequence[date]) -> dict[date, list[dict]]:
    """按**日期**取历史分时, 不受 ``_MAX_BARS`` 20800 根的限制。

    ``stock_bars`` 是「从最新一根往回数 N 根」, 所以 1 分钟 K 只能覆盖最近 87 个
    交易日(20800/240), 更早的无论怎么调参都拿不到。这个接口一天一次调用, 实测
    可回溯到 **2000-06-09**(000001.SZ / 000002.SZ / 600000.SH / 600601.SH 一致,
    1999 及以前全空), 按日递增探测到 2000-06-08 仍为空、2000-06-09 起 240 根。

    注意深度是**主站级**差异: 111 台池子实测 23 台有 2000 年数据、6 台对同一天
    返回空。用老日期取数前必须 ``_select_server(need_history=True, probe_ymd=...)``
    筛主站, 否则会静默拿到空表。

    每天 240 条 ``{price, vol}``: 第 i 条 ``price`` 就是同一天官方 1 分钟 K 第 i 根
    的 ``close``(实测 240/240 逐条对齐, 无偏移), ``vol`` 单位与逐笔一致是**手**。
    代价是只有收盘价和成交量, 没有 OHLC —— 要完整 OHLC 用 :func:`history_ticks`。

    一天 0.02s, 比逐笔快 6 倍, 适合大范围回补分时序列。
    """
    keys = [(day, int(day.strftime("%Y%m%d"))) for day in dates]

    def _run(api):
        out: dict[date, list[dict]] = {}
        for day, key in keys:
            try:
                rows = list(api.get_history_minute_time_data(market, code, key) or [])
            except Exception:  # noqa: BLE001
                rows = []
            _clean_volumes(rows)
            out[day] = rows
        return out

    return _call(_run, need_history=True, probe_ymd=keys[0][1] if keys else None)


def history_ticks(market: int, code: str, dates: Sequence[date]) -> dict[date, list[dict]]:
    """按**日期**取历史逐笔, 可聚合出完整 OHLCV。实测可回溯到 **2000-06-09**。

    ``start`` 同样是从**当日最新一笔往回数**, 所以要把 offset 累加到时间回到开盘;
    单日通常 2~4 页。实测 002172.SZ 2026-10-30 共 4805 笔 / 3 页。早年单日只有
    1100~1800 笔(2000-07 实测 1123 笔/天), 一页即可取完, 因此旧年份反而更快。

    与 :func:`history_minute_time` 同源同深度, 也同为主站级差异, 老日期取数前
    须筛深历史主站。

    ``vol`` 单位是**手**(官方分钟 K 是**股**, 差 100); ``price * vol * 100`` 即成交额。

    **数据源粒度: 这不是逐笔成交, 而是每 3 秒一个成交汇总。** 2025 年 30 只 /
    7290 个股票日全样本实测: 单分钟最多 20 条(60/3), 从未超过, 满格的分钟有 26048 个;
    单日 3939~4735 条, 上限 240 分钟 x 20 = 4800。由此与官方日K 产生三处偏差,
    全部是数据源粒度所致, 不是抓取或解析问题:

    - **成交量**: 61.25% 完全相等, 其余恒差 **+1 手**。官方对总量向下取整
      (74,786,237 股 = 747,862.37 手 -> 747,862), 本接口逐笔四舍五入后累加
      (-> 747,863), 故差额只可能是 0 或 +1, 且比例 38.75% 正好对应末两位小数 >0.5
      的概率。全样本最大差 1 手。
    - **最高/最低价**: 仅 70.77% / 75.84% 相等, 最大差 0.4 元。切片只保留一个价格
      (该切片末价), 切片内的极值成交被丢弃。**要精确 high/low 请用日K, 不要从
      本接口聚合。**
    - **成交额**: 相对误差中位 0.0078% / 最大 0.26%。切片内成交被按「末价 x 切片
      总量」近似, 切片内价格波动越大误差越大。

    开盘价与收盘价 **100% 精确**(最大差 0): 它们是 09:25 / 15:00 那条独立记录,
    不受切片影响。

    ``buyorsell`` 是买卖方向: ``0`` 主动买、``1`` 主动卖、``2`` 中性(含集合竞价,
    也含 3 秒切片内买卖量恰好相等、无法判向的成交)、``8`` 集合竞价虚拟撮合。
    **``8`` 的成交量恒为 0 而价格是试探价**, 用价格算任何指标前必须过滤, 否则
    开盘价只有五成能对上(实测未过滤时 49.93%, 过滤后 100%)。另有少量 0 手但价格
    正常的记录(dir 为 0/1, 全样本 28 条/30 只·年), 算笔数与笔均量前要一并滤掉。
    """
    keys = [(day, int(day.strftime("%Y%m%d"))) for day in dates]

    def _run(api):
        out: dict[date, list[dict]] = {}
        for day, key in keys:
            pages: list[list[dict]] = []
            offset = 0
            for _ in range(_TICK_MAX_PAGES):
                try:
                    chunk = list(api.get_history_transaction_data(market, code, offset, _TICK_PAGE, key) or [])
                except Exception:  # noqa: BLE001
                    chunk = []
                if not chunk:
                    break
                pages.append(chunk)
                offset += len(chunk)
                # 页内按时间升序, 所以 chunk[0] 是本页最早一笔; 回到集合竞价就停。
                if len(chunk) < _TICK_PAGE or str(chunk[0].get("time") or "") <= "09:16":
                    break
            # 第 0 页是最新的一页, 逆序拼接才是当日正序。
            out[day] = [row for page in reversed(pages) for row in page]
        return out

    return _call(_run, need_history=True, probe_ymd=keys[0][1] if keys else None)


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
    if market in _UNSUPPORTED_MARKETS:
        return []

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

    for attempt in (1, 2):
        try:
            return _call(_run)
        except Exception as exc:
            if attempt == 2:
                if type(exc).__name__ == "TdxFunctionCallError":
                    # 主站不提供该市场(北交所 market=2 实测抛这个错)。记下来,
                    # 否则每次同步都要先白等一轮超时、换主站、再失败。
                    _UNSUPPORTED_MARKETS.add(market)
                    logger.info("qatdx 主站不提供 market=%s 的证券列表, 本次进程内跳过", market)
                raise
            # 列表是一次性翻 20+ 页的长事务, 单页超时就会整条失败。重试时把主站
            # 缓存失效, 让 _select_server 重新选一个(而不是钉死在刚超时的那个)。
            logger.warning("qatdx 列表 market=%s 第 %d 次失败, 重选主站重试: %s",
                           market, attempt, exc)
            _CACHE.update(ip=None, port=None, at=0.0)
    return []


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

    # 分笔走的是历史接口(get_history_transaction_data), 必须挑能覆盖 trade_day 的主站,
    # 否则查历史日期时会被免费主站静默返回空。
    return _call(_run, need_history=True, probe_ymd=ymd)


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
    """通达信行业分类 ``tdxhy.cfg``, 走 ``get_block_info`` 按段取回。

    每行格式 ``market|code|行业代码|||申万代码``, 实测 10218 行 / 112 个行业代码。
    主站只给**代码**, 不给中文名: ``incon.dat`` 与 ``tdxzs.cfg`` 实测 size=0,
    ``tdxhy.cfg`` 全文 0 行含中文。名称在通达信客户端本地文件里, 服务端不提供。
    """
    def _run(api):
        meta = api.get_block_info_meta("tdxhy.cfg")
        size = int((meta or {}).get("size") or 0)
        if not size:
            return []
        raw = bytearray()
        # 主站每页回 60000 字节, 按实际回包长度推进才不会重叠。
        while len(raw) < size:
            piece = api.get_block_info("tdxhy.cfg", len(raw), size) or b""
            if not piece:
                break
            raw.extend(piece)
        rows: list[dict] = []
        for line in bytes(raw).decode("gb18030", "replace").splitlines():
            parts = line.split("|")
            if len(parts) < 3:
                continue
            rows.append({
                "market": parts[0].strip(),
                "code": parts[1].strip(),
                "industry_code": parts[2].strip(),
                "sw_code": parts[5].strip() if len(parts) > 5 else "",
            })
        return rows

    return _call(_run)


_MARGIN_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "http://www.szse.cn/"}
# 深交所明细把资金压成「亿元」、数量压成「亿股」两位小数, 还原成元/股。
_SZ_MARGIN_UNIT = 1e8
_SZ_MARGIN_MAX_PAGES = 200
_SZ_MARGIN_CONCURRENCY = 8


def margin(trade_day: date) -> list[dict]:
    """沪深融资融券**明细**(逐只标的)。

    上交所: 当日 xls 里第二个 sheet 是明细(实测约 2000 行 × 8 列, 首行即数据)。
    单元格里的字符串是 UTF-16LE 字节被按单字节解出来的, 读出来每个字符都夹着
    ``\\x00``(代码读成 ``'5\\x001\\x000\\x000\\x005\\x000\\x00'``), 表头也是乱码。
    旧实现靠「证券代码」列名匹配, 必然匹配不到 → 整天返回 0 行。这里按位置解析
    并把单元格还原成正常文本。

    深交所: ``ShowReport/data`` 返回 (总量, 明细) 两个分页表, 明细每页 20 行,
    字段是 jrrz* 缩写。旧实现用的 ``ShowReport.szse``(xlsx) 已经 404。
    """
    iso = trade_day.isoformat()
    day = trade_day.strftime("%Y%m%d")
    rows: list[dict] = []
    sh_url = f"http://www.sse.com.cn/market/dealingdata/overview/margin/a/rzrqjygk{day}.xls"
    try:
        rows.extend(_parse_sh_margin(_http_get(sh_url), iso))
    except Exception as exc:  # noqa: BLE001
        logger.warning("qatdx 上交所融资融券失败: %s", exc)
    try:
        rows.extend(_parse_sz_margin(trade_day))
    except Exception as exc:  # noqa: BLE001
        logger.warning("qatdx 深交所融资融券失败: %s", exc)
    return rows


def _http_get(url: str, timeout: float = 10.0) -> bytes:
    from urllib.request import Request, urlopen

    req = Request(url, headers=_MARGIN_HEADERS)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _restore_cell(value: Any) -> str:
    """还原上交所 xls 的 UTF-16LE 单元格。

    原字节是 UTF-16LE, 被 calamine 按 latin-1 逐字符读出, 于是 "510050" 变成
    "5\\x001\\x000\\x000\\x005\\x000\\x00"。按 latin-1 编回去再用 utf-16-le 解即还原。
    中文简称会因超出 latin-1 而丢失 —— 明细只需要代码和数值, 不影响解析。
    """
    text = str(value if value is not None else "")
    if "\x00" not in text:
        return text.strip()
    try:
        return text.encode("latin-1", "ignore").decode("utf-16-le", "ignore").replace("\x00", "").strip()
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text.replace("\x00", "").strip()


def _parse_sh_margin(payload: bytes, iso: str) -> list[dict]:
    """明细列序: 代码 | 简称 | 融资余额 | 融资买入额 | 融资偿还额 | 融券余量 | 融券卖出量 | 融券偿还量。"""
    import fastexcel

    book = fastexcel.read_excel(payload)
    detail = None
    for name in book.sheet_names:
        frame = book.load_sheet(name).to_polars()
        if frame.width >= 8 and (detail is None or frame.height > detail.height):
            detail = frame
    if detail is None:
        return []
    rows = []
    for record in detail.iter_rows(named=False):
        cells = [_restore_cell(value) for value in record]
        code = cells[0]
        if len(code) != 6 or not code.isdigit():
            continue
        rows.append({
            "symbol": f"{code}.SH",
            "trade_date": iso,
            "leveraged_balance": _num(cells[2]),
            "leveraged_buyout": _num(cells[3]),
            "margin_sell": _num(cells[6]),
            "margin_left": _num(cells[5]),
            "exchange": "SH",
        })
    return rows


def _parse_sz_margin(trade_day: date) -> list[dict]:
    """明细字段: zqdm 代码 | zqjc 简称 | jrrzmr 融资买入 | jrrzye 融资余额 |
    jrrjmc 融券卖出量 | jrrjyl 融券余量 | jrrjye 融券余额 | jrrzrjye 融资融券余额。

    明细每页只给 20 行, 深市 2000 多只标的要翻 100 多页; 串行翻实测 14s, 按批并发
    后约 2s。某页返回空即认为到底, 不再往后要。
    """
    iso = trade_day.isoformat()
    data, page_count = _sz_margin_page(iso, 1)
    if data is None or not data:
        return []
    rows = _sz_margin_rows(data, iso)
    # metadata 里有 pagecount, 用它收敛批次, 不要靠「返回空就停」去多探一批。
    total_pages = min(int(page_count or _SZ_MARGIN_MAX_PAGES), _SZ_MARGIN_MAX_PAGES)
    failed: list[int] = []
    page = 2
    from concurrent.futures import ThreadPoolExecutor

    while page <= total_pages:
        pages = list(range(page, min(page + _SZ_MARGIN_CONCURRENCY, total_pages + 1)))
        with ThreadPoolExecutor(max_workers=len(pages)) as pool:
            results = list(pool.map(lambda index: _sz_margin_page(iso, index)[0], pages))
        reached_end = False
        for index, chunk in zip(pages, results):
            if chunk is None:
                # 请求失败 != 到底。深交所并发一高就偶发超时, 漏掉会把整页标的
                # 静默吞掉(实测少 358 只), 记下来最后补抓。
                failed.append(index)
                continue
            if not chunk:
                reached_end = True
                break
            rows.extend(_sz_margin_rows(chunk, iso))
        if reached_end:
            break
        page += _SZ_MARGIN_CONCURRENCY
    for index in failed:
        chunk, _ = _sz_margin_page(iso, index)
        if chunk:
            rows.extend(_sz_margin_rows(chunk, iso))
    return rows


def _sz_margin_page(iso: str, page: int, attempts: int = 3) -> tuple[list[dict] | None, int]:
    """返回 (明细行, 总页数)。None 表示这次请求失败(区别于 [] 表示该页已到底)。"""
    import json

    url = ("http://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON"
           f"&CATALOGID=1837_xxpl&txtDate={iso}&tab2PAGENO={page}")
    for attempt in range(1, attempts + 1):
        try:
            body = json.loads(_http_get(url, timeout=10.0))
        except Exception as exc:  # noqa: BLE001
            if attempt == attempts:
                logger.warning("qatdx 深交所融资融券第 %d 页失败 %d 次: %s", page, attempts, exc)
                return None, 0
            continue
        detail = next(
            (item for item in body or [] if "明细" in str(item.get("metadata", {}).get("name") or "")),
            None,
        )
        if detail is None:
            return [], 0
        meta = detail.get("metadata") or {}
        return detail.get("data") or [], int(meta.get("pagecount") or 0)
    return None, 0


def _sz_margin_rows(data: list[dict], iso: str) -> list[dict]:
    rows = []
    for item in data:
        code = str(item.get("zqdm") or "").strip().zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        rows.append({
            "symbol": f"{code}.SZ",
            "trade_date": iso,
            "leveraged_balance": _scaled(item.get("jrrzye")),
            "leveraged_buyout": _scaled(item.get("jrrzmr")),
            "margin_sell": _scaled(item.get("jrrjmc")),
            "margin_left": _scaled(item.get("jrrjyl")),
            "exchange": "SZ",
        })
    return rows


def _scaled(value: Any) -> float | None:
    number = _num(value)
    return None if number is None else round(number * _SZ_MARGIN_UNIT, 2)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def reset_server_cache() -> None:
    """测试用。"""
    _CACHE.update(ip=None, port=None, at=0.0, history_ip=None, history_port=None,
                  history_at=0.0, history_from=None)
    _UNSUPPORTED_MARKETS.clear()
