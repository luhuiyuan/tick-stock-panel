"""QATdx provider：pytdx 行情适配到面板契约。

日 K 不复权。除权因子只用除权除息（category==1），字段按每 10 股换成每股后，
用与 TdxQuant 相同的交易所参考价算单事件 ex_factor。成交量保持「手」。

取数通道按品种区分, 不能混用:
- 证券(股票/ETF/债券) 走 ``get_security_bars``;
- **指数必须走 ``get_index_bars``** —— 指数回包尾部多 up_count/down_count 两个
  字段, 用股票解析器读出来是 year=395942 / open=-76257 这类垃圾, 落到日期过滤
  就是「整段为空」, 而且不抛错, 很难发现。
- 分钟超过 K 线窗口(1m 约 87 个交易日)时改走按日期取数的历史通道。

已知的能力边界(源头不提供, 不是本文件的缺陷):
- 财务只填股本快照; 利润表/资产负债表/现金流量表/指标返回空帧。
- 行业只有代码(tdxhy.cfg 全文无中文名), ``industry_name`` 落的就是 ``T1001``
  这类通达信行业代码。
- 北交所: pytdx 这批主站对 market=2 一律抛 TdxFunctionCallError。
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterator
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import polars as pl

from app.data_providers.normalizer import normalize_daily
from app.plugins.minute_bucket import resample_minute, session_bucket, session_minutes
from app.plugins.qatdx import client

logger = logging.getLogger(__name__)

_DATASETS = (
    "daily", "minute", "full_minute", "realtime", "depth5", "instruments",
    "adj_factor", "financial", "transaction", "block", "industry", "margin", "bond",
)

# iter_daily 每多少只标的合并成一帧再上交(见 iter_daily 注释: 一只一帧会让
# 按 date 落地的分区文件数爆炸)。200 只 × 约 250 个交易日 ≈ 5 万行/帧。
_DAILY_CHUNK = 200
_ADJ_SCHEMA = {
    "symbol": pl.String,
    "trade_date": pl.Date,
    "ex_factor": pl.Float64,
    "dividend": pl.Float64,
    "bonus": pl.Float64,
    "allot": pl.Float64,
    "allot_price": pl.Float64,
    "prev_close": pl.Float64,
}
_SHARES_SCHEMA = {
    "symbol": pl.String,
    "period_end": pl.Date,
    "announce_date": pl.Date,
    "total_shares": pl.Float64,
    "float_shares": pl.Float64,
}
_TX_SCHEMA = {
    "symbol": pl.String,
    "datetime": pl.Datetime("us"),
    "price": pl.Float64,
    "volume": pl.Float64,
    "direction": pl.String,
}
_BLOCK_SCHEMA = {
    "symbol": pl.String,
    "block_name": pl.String,
    "block_type": pl.String,
}
_INDUSTRY_SCHEMA = {
    "symbol": pl.String,
    "industry_name": pl.String,
}
_MARGIN_SCHEMA = {
    "symbol": pl.String,
    "trade_date": pl.Date,
    "leveraged_balance": pl.Float64,
    "leveraged_buyout": pl.Float64,
    "margin_sell": pl.Float64,
    "margin_left": pl.Float64,
    "exchange": pl.String,
}
_BARS_PER_DAY = {"1m": 240, "5m": 48, "15m": 16, "30m": 8, "60m": 4}
# 通达信市场号 -> 交易所后缀。行业文件(tdxhy.cfg)每行第一列就是这个市场号。
_MARKET_EXCHANGE = {"0": "SZ", "1": "SH", "2": "BJ"}
# 沪市场内 ETF 代码段: 510~518(含 511 货币/国债 ETF)、520~522、560~566、
# 588/589(科创)。排除 501~508(LOF/REITs) 与 16x/18x。
_ETF_PREFIXES_SH = (
    "510", "511", "512", "513", "515", "516", "517", "518",
    "520", "521", "522",
    "560", "561", "562", "563", "564", "565", "566",
    "588", "589",
)
# get_security_bars 能往回取多远, 以**交易日**计。1m/5m 由 _MAX_BARS(20800 根)决定,
# 15m/30m/60m 是服务器硬性只给 500 个交易日(8000/4000/2000 根, 实测 2026-11-01)。
# 判据必须按交易日算: 60m 每天只有 4 根, 用「需要的根数」永远小于 20800, 会漏判
# 而走到 K 线接口拿回空表。
_KLINE_COVER_DAYS = {"15m": 500, "30m": 500, "60m": 500}
# 通达信回包的时间戳是交易所本地墙钟(naive), 区间端点也必须归一到同一口径。
_CN_TZ = timezone(timedelta(hours=8))


def availability() -> tuple[bool, str]:
    """源码随插件放在 vendor/pytdx，不要求把 pytdx 装进虚拟环境。"""
    hq = client._vendor_root() / "pytdx" / "hq.py"
    if not hq.is_file():
        return False, "缺少插件内 pytdx 源码 (vendor/pytdx)"
    try:
        client._import_api()
    except Exception as exc:  # noqa: BLE001
        return False, f"pytdx 源码无法加载 ({exc})"
    return True, "ok (插件内 pytdx 源码, 无 API Key)"


def _price_limit(symbol: str) -> float:
    code = symbol.split(".")[0]
    if code.startswith(("300", "301", "302", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def _ref_price(prev_close: float, dividend: float, bonus: float, allot: float, allot_price: float) -> float | None:
    denom = 1.0 + bonus + allot
    if denom <= 0 or prev_close <= 0:
        return None
    value = (prev_close - dividend + allot * allot_price) / denom
    if value <= 0:
        return None
    return math.floor(value * 100 + 0.5) / 100


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").date()
        except ValueError:
            return None
    return None


def _bar_datetime(row: dict) -> datetime | None:
    raw = row.get("datetime")
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None)
    text = str(raw or "").strip()
    if text:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text[:19], fmt)
            except ValueError:
                continue
    year, month, day = row.get("year"), row.get("month"), row.get("day")
    if year and month and day:
        return datetime(int(year), int(month), int(day), int(row.get("hour") or 0), int(row.get("minute") or 0))
    return None


def _as_naive_cn(value: datetime) -> datetime:
    """区间端点 → 北京墙钟 naive, 与 ``_bar_datetime`` 的输出同口径。

    上游 ``fetch_minute_single`` 传的是显式带 CN_TZ 的 datetime, 而通达信回包解析
    出来的是 naive; 直接比较会抛 "can't compare offset-naive and offset-aware
    datetimes", 被 ``_try_custom_minute`` 记成源故障后 fail-closed, 页面只剩
    「该日暂无分钟数据（数据源未提供）」—— 源里其实有数据, 是没走到比较之后。
    """
    return value.astimezone(_CN_TZ).replace(tzinfo=None) if value.tzinfo else value


def _shares_to_lots(value: Any) -> float | None:
    """分钟 K 的成交量单位是**股**, 日 K 和面板契约是**手**, 差 100 倍。

    实测 002172.SZ 2026-10-30: 1m/5m/15m/30m/60m 各自求和都是 229,468,900 股,
    同一天日 K 是 2,294,688 手。不换算的话分钟落盘与日K 相差 100 倍, 分时量柱、
    均价、以及「分钟聚合出日K」的校验全部错位。
    """
    if value is None:
        return None
    return float(value) / 100.0


# 分钟 K 的 N 分钟分桶规则(交易时段内各自独立, 跨午休不能连续切)统一定义在
# app.plugins.minute_bucket —— eltdx 与 qatdx 共用同一份, 避免口径分叉。
_FREQ_STEP = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}


def _latest_business_day() -> date:
    """最近一个工作日。设置页「测试」按钮用它取样, 否则周末点测试永远 0 行。

    节假日仍会空, 但那时如实返回 0 行即可 —— 比把「周日没数据」当成故障好。
    """
    day = date.today()
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _weekdays(start: date, end: date) -> list[date]:
    """start~end 的工作日。节假日会取到空数据, 由调用方当空帧处理。"""
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _clock(text: Any) -> time:
    raw = str(text or "").strip()
    return time(int(raw[0:2] or 0), int(raw[3:5] or 0))


def _session_bucket(stamp: datetime, step: int) -> datetime | None:
    """把 1 分钟时间戳归到所属 N 分钟 K 的**结束**标签(实现见 minute_bucket)。"""
    return session_bucket(stamp, step)


def _session_minutes(day: date) -> list[datetime]:
    """一个交易日的 240 个分钟 K 时间戳: 09:31..11:30, 13:01..15:00。"""
    return session_minutes(day)


def _ticks_to_bars(rows: list[dict], symbol: str, day: date,
                   *, with_direction: bool = False) -> pl.DataFrame | None:
    """逐笔按分钟聚合成 1 分钟 K。

    逐笔的 ``time`` 是**成交时刻**, 官方分钟 K 标区间结束, 故整体 +1 分钟: 实测
    按此对齐后 240 根里有 236 根 OHLC 与官方完全一致, 只剩 09:30 集合竞价、11:29、
    14:59 三个边界不同。``vol`` 是手, ``price * vol * 100`` 即成交额(元)。

    上游不是逐笔成交而是**每 3 秒一个成交汇总**(见 :func:`client.history_ticks`),
    所以聚合出的 high/low 会丢秒内极值: 全样本(2025 年 30 只 / 7290 个股票日)与
    官方日K 相比, 最高/最低价只有 70.77% / 75.84% 相等、最大差 0.4 元。要精确
    high/low 请用日K。成交量差值恒为 0 或 +1 手(官方对总量向下取整、逐笔逐笔
    四舍五入后累加)。

    ``buyorsell=8`` 的集合竞价虚拟撮合记录在此被丢弃: 成交量恒为 0 而 price 是竞价
    过程的试探价。09:15~09:24 那批本来就被开盘边界挡掉, 14:57~14:59 那批若不显式
    过滤会被 `min(stamp, last_bar)` 夹进 15:00 那根, 拽偏真实收盘竞价的 open/low
    —— 实测 19.7% 的股票日中招(000001.SZ 2025-07-22 未过滤时 open/low=12.48,
    官方应为 12.49)。

    ``with_direction`` 打开时多出 ``buy_volume`` / ``sell_volume`` 两列(单位同
    ``volume``, 即手), 来自逐笔的 ``buyorsell`` 字段: 0=主动买、1=主动卖、
    其余视为中性(不计入任何一边)。这是逐笔相对分钟 K 唯一会丢的信息, 做资金流类
    指标必须保留; 但它是通达信按成交价与盘口位置推断的, 不是交易所标记。
    """
    if not rows:
        return None
    # 官方分钟 K 的首末根吸收竞价成交: 09:25 的集合竞价算进 09:31, 15:00 的收盘
    # 竞价算进 15:00。实测 002172.SZ 2026-10-30 官方首根 open=5.61 正是 09:25 那笔
    # 集合竞价价, vol=412,597 手 = 09:25 的 105,603 手 + 09:30 的 20 笔。
    # 09:15~09:24 是集合竞价过程中的虚拟撮合(价格不代表开盘价), 15:00 之后的
    # 15:05~15:24 是盘后定价交易, 两者都不属于任何分钟 K, 必须丢弃而不是并进首末根。
    first_bar = datetime.combine(day, time(9, 31))
    last_bar = datetime.combine(day, time(15, 0))
    # 午休边界: 11:30 的成交 +1 分钟后落到 11:31, 但官方没有 11:31 这根 —— 上午
    # 收盘那笔并进 11:30。不夹的话 11:30 那根会被顶成 11:31(实测 43% 的股票日会
    # 多出一根幽灵 bar), 且 11:30 本身反而空掉。
    morning_close = datetime.combine(day, time(11, 30))
    afternoon_open = datetime.combine(day, time(13, 1))
    stamps, prices, volumes = [], [], []
    directions: list[int] = []
    for row in rows:
        price = float(row.get("price") or 0.0)
        clock = _clock(row.get("time"))
        minute_of_day = clock.hour * 60 + clock.minute
        # 注意不能写 `int(row.get("buyorsell") or 2)`: 主动买就是 0, 会被 or 当成
        # 假值替换成中性, 把买单全部丢掉。
        raw_dir = row.get("buyorsell")
        direction = 2 if raw_dir is None else int(raw_dir)
        # 集合竞价虚拟撮合(buyorsell=8): 成交量恒为 0, price 是竞价过程中的试探价,
        # 不属于任何一根分钟 K。09:15~09:24 那批已被下面的 `minute_of_day < 565` 挡掉,
        # 但 14:57~14:59 那批落在合法区间内, 会被 `min(stamp, last_bar)` 夹进 15:00
        # 那根, 把真实收盘竞价的 open/low 拽偏 —— 实测 19.7% 的股票日中招
        # (000001.SZ 2025-07-22: open/low 被拉成 12.48, 官方应为 12.49)。
        if direction == 8:
            continue
        if price <= 0 or minute_of_day < 565 or minute_of_day > 900:
            continue
        stamp = datetime.combine(day, clock) + timedelta(minutes=1)
        if morning_close < stamp < afternoon_open:
            stamp = morning_close
        stamps.append(min(max(stamp, first_bar), last_bar))
        prices.append(price)
        volumes.append(float(row.get("vol") or 0.0))
        directions.append(direction)
    if not stamps:
        return None
    frame = pl.DataFrame({"dt": stamps, "price": prices, "vol": volumes})
    aggs = [
        pl.col("price").first().alias("open"),
        pl.col("price").max().alias("high"),
        pl.col("price").min().alias("low"),
        pl.col("price").last().alias("close"),
        pl.col("vol").sum().alias("volume"),
        (pl.col("price") * pl.col("vol") * 100.0).sum().alias("amount"),
    ]
    if with_direction:
        frame = frame.with_columns(pl.Series("dir", directions, dtype=pl.Int32))
        aggs += [
            pl.when(pl.col("dir") == 0).then(pl.col("vol")).otherwise(0.0).sum().alias("buy_volume"),
            pl.when(pl.col("dir") == 1).then(pl.col("vol")).otherwise(0.0).sum().alias("sell_volume"),
        ]
    bars = frame.group_by("dt", maintain_order=True).agg(*aggs).sort("dt")
    columns = ["symbol", "dt", "open", "high", "low", "close", "volume", "amount"]
    if with_direction:
        columns += ["buy_volume", "sell_volume"]
    return bars.with_columns(pl.lit(symbol).alias("symbol")).select(columns)


def _ticks_frame_to_bars(frame: pl.DataFrame, *, with_direction: bool = False) -> pl.DataFrame:
    """向量化的逐笔->分钟聚合, 输出口径与 :func:`_ticks_to_bars` 完全一致。

    与逐日构造 ``list[dict]`` 的那个版本相比, 这里直接吃**带 date 列的 DataFrame**,
    可以一次处理一只股票的全历史(按 ``symbol, dt`` 分组), 省掉 Python 层面的
    逐行循环。实测 000001.SZ 2025 全年 58,068 根 bar 与 :func:`_ticks_to_bars`
    **逐根零差异**, 耗时 5.31s -> 0.44s(12x); 从备份 zip 重建全市场分钟表因此
    从 7 小时降到约 40 分钟。

    需要的列: ``symbol``(str)、``date``(Date)、``time``(str, ``HH:MM``)、
    ``price``、``volume``、``direction``。边界规则与 :func:`_ticks_to_bars`
    一脉相承: 丢弃 ``direction == 8``(集合竞价虚拟撮合)、丢弃 09:25 前与 15:00 后、
    11:30 与 13:01 之间的午休成交夹回 11:30、整体首末夹到 09:31 / 15:00。
    """
    if frame.is_empty():
        return pl.DataFrame()
    d = frame.with_columns([
        pl.col("time").str.slice(0, 2).cast(pl.Int32).alias("_hh"),
        pl.col("time").str.slice(3, 2).cast(pl.Int32).alias("_mi"),
    ]).with_columns((pl.col("_hh") * 60 + pl.col("_mi")).alias("_mod"))
    d = d.filter(
        (pl.col("direction") != 8)
        & (pl.col("price") > 0)
        & (pl.col("_mod") >= 565)
        & (pl.col("_mod") <= 900)
    )
    if d.is_empty():
        return pl.DataFrame()

    y, mo, dy = pl.col("date").dt.year(), pl.col("date").dt.month(), pl.col("date").dt.day()
    d = d.with_columns(
        (pl.datetime(y, mo, dy, pl.col("_hh"), pl.col("_mi")) + pl.duration(minutes=1)).alias("dt")
    )
    morning_close = pl.datetime(y, mo, dy, 11, 30)
    afternoon_open = pl.datetime(y, mo, dy, 13, 1)
    d = d.with_columns(
        pl.when((pl.col("dt") > morning_close) & (pl.col("dt") < afternoon_open))
        .then(morning_close).otherwise(pl.col("dt")).alias("dt")
    )
    first_bar = pl.datetime(y, mo, dy, 9, 31)
    last_bar = pl.datetime(y, mo, dy, 15, 0)
    d = d.with_columns(
        pl.min_horizontal(pl.max_horizontal(pl.col("dt"), first_bar), last_bar).alias("dt")
    )
    # open/close 要取原始行序的首末, 不能按时间再排一次: 同一分钟内的多条记录
    # 顺序就是数据源给的顺序, 排序会打乱它。
    d = d.with_row_index("_rid")
    aggs = [
        pl.col("price").sort_by("_rid").first().alias("open"),
        pl.col("price").max().alias("high"),
        pl.col("price").min().alias("low"),
        pl.col("price").sort_by("_rid").last().alias("close"),
        pl.col("volume").sum().alias("volume"),
        (pl.col("price") * pl.col("volume") * 100.0).sum().alias("amount"),
    ]
    if with_direction:
        aggs += [
            pl.when(pl.col("direction") == 0).then(pl.col("volume")).otherwise(0.0).sum().alias("buy_volume"),
            pl.when(pl.col("direction") == 1).then(pl.col("volume")).otherwise(0.0).sum().alias("sell_volume"),
        ]
    bars = d.group_by(["symbol", "dt"], maintain_order=True).agg(*aggs).sort(["symbol", "dt"])
    columns = ["symbol", "dt", "open", "high", "low", "close", "volume", "amount"]
    if with_direction:
        columns += ["buy_volume", "sell_volume"]
    return bars.select(columns)


def _resample_bars(bars: pl.DataFrame, step: int) -> pl.DataFrame:
    """1 分钟 K 重采样成 N 分钟, 用交易时段内的真实分钟数分桶(实现见 minute_bucket)。"""
    return resample_minute(bars, step, time_col="dt")


def _asset_kind(code: str, asset_type: str, market: int | None = None,
                name: str | None = None) -> bool:
    """判断代码是否属于指定品种。

    market 传入时必须按市场区分: 通达信的沪市列表里同时含 000xxx(上证系列指数)、
    880xxx/881xxx/999999(板块与统计指数), 只按代码前缀判断会把它们当成股票,
    经标的池写进 kline_daily。市场号: 沪=1, 深=0, 北=2。
    """
    if asset_type == "index":
        if market is not None:
            return code.startswith("399") if market == 0 else code.startswith("000")
        return code.startswith("399") or code.startswith("000")
    if asset_type == "etf":
        # 不能按简称判: 通达信列表里 ETF 是短名 —— 159915 叫「创业板」、588000 叫
        # 「科创50」、513050 叫「中概互联」, 一个 "ETF" 都不带; 反过来 16/18 段是
        # LOF 与封闭式基金。所以只能按代码段认, 而且**必须分市场**: 520/563 在沪市
        # 是 ETF, 在深市是债券("26伊犁02"/"山西2430"), 不区分会把两只债券收进来。
        if market == 0:
            # 深市 15x 段: 实测只有 158(跨境/商品) 与 159(主流 ETF)。
            return code.startswith("15")
        if market == 1:
            return code.startswith(_ETF_PREFIXES_SH)
        return code.startswith(_ETF_PREFIXES_SH) or code.startswith("15")
    if asset_type == "bond":
        return code.startswith((
            "101", "104", "105", "106", "107", "108", "109", "110", "111", "112",
            "113", "114", "115", "116", "117", "118", "119", "120", "122", "123",
            "127", "128", "130", "131", "132",
        ))
    if asset_type == "stock":
        if market == 1:
            # 沪市股票只有 60x(主板)/68x(科创板), 000xxx 是上证指数、88xxxx 是板块统计
            return code.startswith(("60", "68"))
        if market == 0:
            # 深市股票 000/001/002/003 + 300/301/302(创业板); 399xxx 是指数
            return code.startswith(("00", "30"))
        if market == 2:
            return code.startswith(("43", "83", "87", "88", "92"))
        return code.startswith(("00", "30", "60", "68", "43", "83", "87", "88", "92"))
    return False


def _symbol_of_block_code(code: str) -> str | None:
    text = "".join(ch for ch in str(code or "") if ch.isdigit()).zfill(6)[-6:]
    if len(text) != 6:
        return None
    if text.startswith(("43", "83", "87", "88", "92", "4", "8")):
        return f"{text}.BJ"
    if text.startswith(("5", "6", "9")):
        return f"{text}.SH"
    if text.startswith(("0", "1", "3")):
        return f"{text}.SZ"
    return None


class QatdxProvider:
    name = "qatdx"
    minute_history_days = 60

    def __init__(self) -> None:
        self.builtin = True
        self.datasets = dict.fromkeys(_DATASETS)
        self.config = type("ProviderConfig", (), {"datasets": self.datasets})()
        # symbol -> 简称。pytdx 的报价回包不含名称, 但证券列表里有; 同步维表时
        # 顺手存一份, 实时接口就能把契约里的 name 填上, 不用每轮都重拉 2.7 万条列表。
        self._names: dict[str, str] = {}

    def close(self) -> None:
        return None

    def get_instruments(self, asset_type: str = "stock") -> list[dict]:
        rows: list[dict] = []
        markets = (0, 1, 2) if asset_type in {"stock", "bond"} else (0, 1)
        for market in markets:
            try:
                listed = client.security_list(market)
            except Exception as exc:  # noqa: BLE001
                logger.warning("qatdx 列表失败 market=%s: %s", market, exc)
                continue
            for item in listed:
                code = str(item.get("code") or "").zfill(6)
                name = item.get("name") or code
                if not _asset_kind(code, asset_type, market, name):
                    continue
                symbol = client.to_symbol(market, code)
                if symbol is None:
                    continue
                self._names[symbol] = name
                rows.append({
                    "symbol": symbol,
                    "name": name,
                    "code": code,
                    "exchange": symbol.split(".")[1],
                    "asset_type": asset_type,
                    "source": self.name,
                })
        return rows

    def iter_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> Iterator[pl.DataFrame]:
        """逐标的取日K, 每 _DAILY_CHUNK 只拼成一帧再交给上层。

        不要一只一帧: 上游 `_persist_daily_chunks` 会按 date 拆分落地, 单标的帧会
        变成「一只股票 × 每个交易日」一个小文件, 全市场首次同步即 5000×250 ≈ 125 万
        个 parquet, 写入速度直接卡死在每小时几十只。按批合并后文件数按批数收敛。
        """
        total = len(symbols or [])
        if total == 0:
            if on_chunk_done:
                on_chunk_done(0, 0)
            return
        pending: list[pl.DataFrame] = []
        for index, symbol in enumerate(symbols, start=1):
            try:
                frame = self._daily_one(symbol, start_time, end_time, asset_type)
            except Exception as exc:  # noqa: BLE001
                logger.warning("qatdx 日K %s 失败: %s", symbol, exc)
                frame = pl.DataFrame()
            if not frame.is_empty():
                pending.append(frame)
            if pending and (len(pending) >= _DAILY_CHUNK or index == total):
                yield pl.concat(pending, how="diagonal_relaxed")
                pending = []
            if on_chunk_done:
                on_chunk_done(index, total)

    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        frames = [
            frame for frame in self.iter_daily(
                symbols, start_time, end_time, asset_type, on_chunk_done,
            ) if not frame.is_empty()
        ]
        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed")

    def _daily_one(self, symbol: str, start_time: datetime | None, end_time: datetime | None, asset_type: str) -> pl.DataFrame:
        parsed = client.split_symbol(symbol, index=asset_type == "index")
        if parsed is None:
            return pl.DataFrame()
        market, code = parsed
        end = end_time.date() if start_time or end_time else date.today()
        if end_time:
            end = end_time.date()
        start = start_time.date() if start_time else end - timedelta(days=365)
        # count 是「从最新一根往回数」的根数, 跨度必须从**今天**算到 start。
        # 用查询区间宽度算(如 2015-01~2015-03 只需 69 根)取回来的是最近 69 个
        # 交易日, 再按区间过滤就恒为空。日线 8000 根实测可回溯到 1991 年。
        count = min(8000, max((date.today() - start).days + 10, 5))
        fetch = client.index_bars if asset_type == "index" else client.stock_bars
        raw = fetch(market, code, 9, count)
        rows = []
        for item in raw:
            stamp = _bar_datetime(item)
            if stamp is None:
                continue
            if stamp.date() < start or stamp.date() > end:
                continue
            if float(item.get("open") or 0) == 0:
                continue
            rows.append({
                "symbol": f"{code}.{symbol.split('.')[1]}",
                "date": stamp.date(),
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": item.get("close"),
                "volume": item.get("vol", item.get("volume")),
                "amount": item.get("amount"),
            })
        return normalize_daily(rows, default_symbol=symbol, source=self.name)

    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        freq: str = "1m",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        category = client.bar_category(freq)
        if category is None or category == 9:
            return pl.DataFrame()
        freq_key = "1m" if category == 8 else freq
        per_day = _BARS_PER_DAY.get(freq_key, 240)
        end = _as_naive_cn(end_time) if end_time else datetime.now()
        start = _as_naive_cn(start_time) if start_time else (end - timedelta(days=5))
        # count 是「从最新一根往回数」的根数, 不是区间跨度: 只按查询区间的宽度算,
        # 取回来的永远是最近这一天, 过滤历史日期后恒为空。要从今天倒推到 start。
        span_days = max(1, (date.today() - start.date()).days + 1)
        # K 线接口是「从最新一根往回数」, 越界后无论怎么调 count 都只有最近那一批:
        # 1 分钟约 87 个交易日, 其余周期见 _KLINE_COVER_DAYS。超出覆盖深度就改走
        # 按日期取数的历史通道。
        cover_days = _KLINE_COVER_DAYS.get(freq_key) or (client._MAX_BARS // per_day)
        if int(span_days * 0.8) + 2 > cover_days:
            return self._minute_from_history(symbols or [], start, end, asset_type, freq_key, on_chunk_done)
        count = min(client._MAX_BARS, max(per_day, (int(span_days * 0.8) + 2) * per_day))
        frames: list[pl.DataFrame] = []
        total = len(symbols or [])
        for index, symbol in enumerate(symbols or [], start=1):
            parsed = client.split_symbol(symbol, index=asset_type == "index")
            if parsed is not None:
                market, code = parsed
                try:
                    fetch = client.index_bars if asset_type == "index" else client.stock_bars
                    raw = fetch(market, code, category, count)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("qatdx 分钟 %s 失败: %s", symbol, exc)
                    raw = []
                rows = []
                for item in raw:
                    stamp = _bar_datetime(item)
                    if stamp is None or stamp < start or stamp > end:
                        continue
                    rows.append({
                        "symbol": symbol,
                        "datetime": stamp,
                        "open": item.get("open"),
                        "high": item.get("high"),
                        "low": item.get("low"),
                        "close": item.get("close"),
                        "volume": _shares_to_lots(item.get("vol", item.get("volume"))),
                        "amount": item.get("amount"),
                    })
                if rows:
                    frames.append(pl.DataFrame(rows).with_columns(
                        pl.col("datetime").cast(pl.Datetime("us")),
                        pl.col("open").cast(pl.Float64),
                        pl.col("high").cast(pl.Float64),
                        pl.col("low").cast(pl.Float64),
                        pl.col("close").cast(pl.Float64),
                        pl.col("volume").cast(pl.Float64),
                        pl.col("amount").cast(pl.Float64),
                    ))
            if on_chunk_done:
                on_chunk_done(index, total)
        if total == 0 and on_chunk_done:
            on_chunk_done(0, 0)
        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed")

    def _minute_from_history(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        asset_type: str,
        freq_key: str,
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        """用历史逐笔重建分钟 K, 走 ``get_history_transaction_data``(按日期取数)。

        ``get_security_bars`` 的 20800 根上限让 1 分钟只能覆盖 87 个交易日, 而逐笔
        接口按日期取, 实测可回溯到 2008-01-02。代价是每天要翻 2~4 页(约 0.12s),
        所以只在频率确实超出 K 线窗口时启用, 不做「先试 K 线再补历史」的两段拼接。

        对外契约与 :meth:`get_minute` 一致(含完整 OHLC + 成交额), 但数值是逐笔聚合
        出来的: 与官方分钟 K 相比, 按 +1 分钟对齐后 240 根里 236 根完全一致。
        """
        step = _FREQ_STEP.get(freq_key, 1)
        days = _weekdays(start.date(), min(end.date(), date.today()))
        frames: list[pl.DataFrame] = []
        total = len(symbols)
        for index, symbol in enumerate(symbols, start=1):
            parsed = client.split_symbol(symbol, index=asset_type == "index")
            if parsed is not None:
                market, code = parsed
                try:
                    ticks = client.history_ticks(market, code, days)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("qatdx 历史分钟 %s 失败: %s", symbol, exc)
                    ticks = {}
                for day, items in ticks.items():
                    bars = _ticks_to_bars(items, symbol, day)
                    if bars is None:
                        continue
                    bars = _resample_bars(bars, step)
                    bars = bars.filter((pl.col("dt") >= start) & (pl.col("dt") <= end))
                    if bars.height:
                        frames.append(bars)
            if on_chunk_done:
                on_chunk_done(index, total)
        if total == 0 and on_chunk_done:
            on_chunk_done(0, 0)
        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed").sort(["symbol", "dt"]).rename(
            {"dt": "datetime"}
        ).with_columns(
            pl.col("datetime").cast(pl.Datetime("us")),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64),
            pl.col("amount").cast(pl.Float64),
        )

    def get_minute_line(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
    ) -> pl.DataFrame:
        """历史分时序列(只有 close + volume), 走 ``get_history_minute_time_data``。

        每天一次调用 0.02s, 比逐笔重建快 6 倍, 适合大范围回补分时图; 返回
        ``symbol / datetime / close / volume``, **没有 OHLC 和成交额** —— 需要完整
        分钟 K 就用 :meth:`get_minute`。

        实测该接口第 i 条 ``price`` 就是官方 1 分钟 K 第 i 根的 ``close``(240/240
        逐条对齐无偏移), ``vol`` 单位是手。可回溯到 2007-01-04。
        """
        end = _as_naive_cn(end_time) if end_time else datetime.now()
        start = _as_naive_cn(start_time) if start_time else (end - timedelta(days=5))
        days = _weekdays(start.date(), min(end.date(), date.today()))
        frames: list[pl.DataFrame] = []
        for symbol in symbols or []:
            parsed = client.split_symbol(symbol, index=asset_type == "index")
            if parsed is None:
                continue
            market, code = parsed
            try:
                series = client.history_minute_time(market, code, days)
            except Exception as exc:  # noqa: BLE001
                logger.warning("qatdx 历史分时 %s 失败: %s", symbol, exc)
                continue
            for day, items in series.items():
                stamps = _session_minutes(day)
                if not items:
                    continue
                if len(items) != len(stamps):
                    # 半天市或停牌会让条数对不上, 这时序号无法映射到时刻, 跳过而不是猜。
                    logger.debug("qatdx %s %s 分时 %d 条, 与 240 分钟不对齐, 跳过", symbol, day, len(items))
                    continue
                frame = pl.DataFrame({
                    "symbol": symbol,
                    "datetime": stamps,
                    "close": [float(item.get("price") or 0.0) for item in items],
                    "volume": [float(item.get("vol") or 0.0) for item in items],
                })
                frame = frame.filter((pl.col("datetime") >= start) & (pl.col("datetime") <= end))
                if frame.height:
                    frames.append(frame)
        if not frames:
            return pl.DataFrame()
        return pl.concat(frames, how="diagonal_relaxed").sort(["symbol", "datetime"]).with_columns(
            pl.col("datetime").cast(pl.Datetime("us")),
            pl.col("close").cast(pl.Float64),
            pl.col("volume").cast(pl.Float64),
        )

    def get_intraday_batch(self, symbols: list[str], count: int = 240, asset_type: str = "stock") -> pl.DataFrame:
        today = date.today()
        start = datetime.combine(today, time(9, 0))
        end = datetime.combine(today, time(15, 10))
        return self.get_minute(symbols, start, end, asset_type=asset_type, freq="1m")

    def get_realtime(self, universes: list[str] | None = None, symbols: list[str] | None = None) -> list[dict]:
        del universes
        try:
            targets = list(symbols or [])
            if not targets:
                targets = [row["symbol"] for row in self.get_instruments("stock")]
            return self._quotes(targets, index=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("qatdx 实时快照失败: %s", exc)
            return []

    def get_realtime_indices(self, symbols: list[str]) -> list[dict] | None:
        try:
            return self._quotes(list(symbols or []), index=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("qatdx 指数快照失败: %s", exc)
            return None

    def get_depth_batch(self, symbols: list[str]) -> dict[str, dict]:
        books: dict[str, dict] = {}
        parsed_pairs = []
        symbol_by_code = {}
        for symbol in symbols or []:
            parsed = client.split_symbol(symbol)
            if parsed is None:
                continue
            market, code = parsed
            parsed_pairs.append((market, code))
            symbol_by_code[code] = symbol
        try:
            rows = client.quotes(parsed_pairs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("qatdx 五档失败: %s", exc)
            return {}
        now_ms = int(datetime.now().timestamp() * 1000)
        for item in rows:
            code = str(item.get("code") or "").zfill(6)
            symbol = symbol_by_code.get(code)
            if symbol is None:
                continue
            books[symbol] = {
                "bid_prices": [item.get(f"bid{i}") for i in range(1, 6)],
                "bid_volumes": [item.get(f"bid_vol{i}") for i in range(1, 6)],
                "ask_prices": [item.get(f"ask{i}") for i in range(1, 6)],
                "ask_volumes": [item.get(f"ask_vol{i}") for i in range(1, 6)],
                "timestamp": now_ms,
            }
        return books

    def _quotes(self, symbols: list[str], *, index: bool) -> list[dict]:
        pairs = []
        wanted = {}
        for symbol in symbols:
            parsed = client.split_symbol(symbol, index=index)
            if parsed is None:
                continue
            market, code = parsed
            pairs.append((market, code))
            wanted[code] = symbol
        records = []
        for item in client.quotes(pairs):
            code = str(item.get("code") or "").zfill(6)
            symbol = wanted.get(code)
            if symbol is None:
                continue
            price = _float(item.get("price"))
            prev = _float(item.get("last_close"))
            high = _float(item.get("high"))
            low = _float(item.get("low"))
            change = None if price is None or prev is None else price - prev
            change_pct = None if change is None or not prev else change / prev
            # 振幅 = (最高 - 最低) / 昨收, 三个数都在回包里, 没有理由留空。
            amplitude = None if high is None or low is None or not prev else (high - low) / prev
            records.append({
                "symbol": symbol,
                "last_price": price,
                "prev_close": prev,
                "open": _float(item.get("open")),
                "high": high,
                "low": low,
                "volume": _float(item.get("vol")),
                "amount": _float(item.get("amount")),
                "change_pct": change_pct,
                "change_amount": change,
                "timestamp": int(datetime.now().timestamp() * 1000),
                "name": self._names.get(symbol),
                "amplitude": amplitude,
                "turnover_rate": None,
                "session": None,
            })
        return records

    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        if asset_type != "stock" or not symbols:
            if on_chunk_done:
                on_chunk_done(0, len(symbols or []))
            return pl.DataFrame(schema=_ADJ_SCHEMA)
        start = start_time.date() if start_time else date(1990, 1, 1)
        end = end_time.date() if end_time else date.today()
        out: list[dict] = []
        for index, symbol in enumerate(symbols, start=1):
            try:
                out.extend(self._factor_rows(symbol, start, end))
            except Exception as exc:  # noqa: BLE001
                logger.warning("qatdx 除权因子 %s 失败: %s", symbol, exc)
            if on_chunk_done:
                on_chunk_done(index, len(symbols))
        if not out:
            return pl.DataFrame(schema=_ADJ_SCHEMA)
        return pl.DataFrame(out, schema=_ADJ_SCHEMA).unique(
            subset=["symbol", "trade_date"], keep="last",
        ).sort(["symbol", "trade_date"])

    def _factor_rows(self, symbol: str, start: date, end: date) -> list[dict]:
        parsed = client.split_symbol(symbol)
        if parsed is None:
            return []
        market, code = parsed
        events = []
        for item in client.xdxr(market, code):
            if int(item.get("category") or 0) != 1:
                continue
            ex_date = date(int(item["year"]), int(item["month"]), int(item["day"]))
            if ex_date < start or ex_date > end:
                continue
            events.append((ex_date, item))
        if not events:
            return []
        first = min(item[0] for item in events)
        bars = client.stock_bars(market, code, 9, min(8000, max((end - first).days + 30, 30)))
        closes: dict[date, float] = {}
        for item in bars:
            stamp = _bar_datetime(item)
            close = _float(item.get("close"))
            if stamp is not None and close is not None and close > 0:
                closes[stamp.date()] = close
        days = sorted(closes)
        merged: dict[date, dict] = {}
        for ex_date, item in events:
            slot = merged.setdefault(ex_date, {"fenhong": 0.0, "songzhuangu": 0.0, "peigu": 0.0, "peigujia": 0.0})
            slot["fenhong"] += _float(item.get("fenhong")) or 0.0
            slot["songzhuangu"] += _float(item.get("songzhuangu")) or 0.0
            slot["peigu"] += _float(item.get("peigu")) or 0.0
            price = _float(item.get("peigujia"))
            if price:
                slot["peigujia"] = price
        rows = []
        for ex_date, slot in sorted(merged.items()):
            prev_days = [day for day in days if day < ex_date]
            if not prev_days:
                continue
            prev_close = closes[prev_days[-1]]
            dividend = slot["fenhong"] / 10.0
            bonus = slot["songzhuangu"] / 10.0
            allot = slot["peigu"] / 10.0
            allot_price = slot["peigujia"]
            ref = _ref_price(prev_close, dividend, bonus, allot, allot_price)
            if ref is None:
                continue
            factor = prev_close / ref
            ex_days = [day for day in days if day >= ex_date]
            if ex_days:
                ret = closes[ex_days[0]] / (prev_close / factor) - 1.0
                if abs(ret) > _price_limit(symbol) + 0.02:
                    logger.warning("qatdx 除权因子自检剔除 %s %s: %.1f%%", symbol, ex_date, ret * 100)
                    continue
            rows.append({
                "symbol": symbol,
                "trade_date": ex_date,
                "ex_factor": factor,
                "dividend": dividend,
                "bonus": bonus,
                "allot": allot,
                "allot_price": allot_price,
                "prev_close": prev_close,
            })
        return rows

    def get_financials(self, table: str, symbols: list[str], latest_only: bool = False) -> pl.DataFrame:
        del latest_only
        if table != "shares":
            return pl.DataFrame()
        rows = []
        for symbol in symbols or []:
            parsed = client.split_symbol(symbol)
            if parsed is None:
                continue
            try:
                info = client.finance(*parsed)
            except Exception as exc:  # noqa: BLE001
                logger.warning("qatdx 财务快照 %s 失败: %s", symbol, exc)
                continue
            float_raw = info.get("liutongguben")
            total_raw = info.get("zongguben")
            already_shares = _shares_already_in_shares(float_raw, total_raw)
            float_shares = _shares(float_raw, already_shares=already_shares)
            total_shares = _shares(total_raw, already_shares=already_shares)
            if float_shares is None:
                continue
            as_of = _as_date(info.get("updated_date")) or date.today()
            rows.append({
                "symbol": symbol,
                "period_end": as_of,
                "announce_date": as_of,
                "total_shares": total_shares if total_shares is not None else float_shares,
                "float_shares": float_shares,
            })
        if not rows:
            return pl.DataFrame(schema=_SHARES_SCHEMA)
        return pl.DataFrame(rows, schema=_SHARES_SCHEMA)

    def get_transactions(self, symbols: list[str], trade_day: date) -> pl.DataFrame:
        rows = []
        for symbol in symbols or []:
            code = symbol.split(".")[0]
            # 000001.SZ 是股票；000001.SH / 399xxx 才是指数。
            index = code.startswith("399") or (symbol.endswith(".SH") and code.startswith("000"))
            parsed = client.split_symbol(symbol, index=index)
            if parsed is None:
                continue
            market, code = parsed
            try:
                raw = client.transactions(market, code, trade_day)
            except Exception as exc:  # noqa: BLE001
                logger.warning("qatdx 分笔 %s 失败: %s", symbol, exc)
                continue
            for item in raw:
                stamp = _tick_time(trade_day, item.get("time"))
                if stamp is None:
                    continue
                rows.append({
                    "symbol": symbol,
                    "datetime": stamp,
                    "price": _float(item.get("price")),
                    "volume": _float(item.get("vol")),
                    "direction": _direction(item.get("buyorsell")),
                })
        if not rows:
            return pl.DataFrame(schema=_TX_SCHEMA)
        return pl.DataFrame(rows, schema=_TX_SCHEMA)

    def get_blocks(self) -> pl.DataFrame:
        try:
            raw = client.blocks()
        except Exception as exc:  # noqa: BLE001
            logger.warning("qatdx 板块失败: %s", exc)
            return pl.DataFrame(schema=_BLOCK_SCHEMA)
        rows = []
        for item in raw:
            symbol = _symbol_of_block_code(item.get("code"))
            name = item.get("blockname") or item.get("block_name")
            if symbol is None or not name:
                continue
            rows.append({
                "symbol": symbol,
                "block_name": str(name),
                "block_type": str(item.get("block_type") or ""),
            })
        if not rows:
            return pl.DataFrame(schema=_BLOCK_SCHEMA)
        return pl.DataFrame(rows, schema=_BLOCK_SCHEMA).unique(subset=["symbol", "block_name", "block_type"])

    def get_industries(self) -> pl.DataFrame:
        """通达信行业分类。

        主站只提供行业**代码**(``T1001`` 这类), 中文名在客户端本地文件里, 服务端
        不给(``incon.dat``/``tdxzs.cfg`` 实测为空)。所以 ``industry_name`` 落的就是
        行业代码: 可用于行业聚合与筛选, 但不是可读名称。需要中文名请改用别的源。
        """
        try:
            raw = client.industries()
        except Exception as exc:  # noqa: BLE001
            logger.warning("qatdx 行业失败: %s", exc)
            return pl.DataFrame(schema=_INDUSTRY_SCHEMA)
        rows = []
        for item in raw:
            code = str(item.get("code") or "").strip()
            industry = str(item.get("industry_code") or "").strip()
            exchange = _MARKET_EXCHANGE.get(str(item.get("market") or "").strip())
            if len(code) != 6 or not code.isdigit() or not industry or exchange is None:
                continue
            rows.append({"symbol": f"{code}.{exchange}", "industry_name": industry})
        if not rows:
            return pl.DataFrame(schema=_INDUSTRY_SCHEMA)
        return pl.DataFrame(rows, schema=_INDUSTRY_SCHEMA).unique(subset=["symbol", "industry_name"])

    def get_margin(self, trade_day: date | None = None) -> pl.DataFrame:
        day = trade_day or date.today()
        try:
            raw = client.margin(day)
        except Exception as exc:  # noqa: BLE001
            logger.warning("qatdx 融资融券失败: %s", exc)
            return pl.DataFrame(schema=_MARGIN_SCHEMA)
        if not raw:
            return pl.DataFrame(schema=_MARGIN_SCHEMA)
        frame = pl.DataFrame(raw)
        if "trade_date" in frame.columns and frame.schema["trade_date"] == pl.String:
            frame = frame.with_columns(pl.col("trade_date").str.to_date(strict=False))
        elif "trade_date" in frame.columns and frame.schema["trade_date"] != pl.Date:
            frame = frame.with_columns(pl.col("trade_date").cast(pl.Date, strict=False))
        return frame

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        sample = symbols or ["000001.SZ"]
        try:
            if dataset == "daily":
                frame = self.get_daily(sample[:1], datetime.now() - timedelta(days=10), datetime.now(), "stock")
            elif dataset == "minute":
                frame = self.get_minute(sample[:1], datetime.now() - timedelta(days=2), datetime.now(), freq="1m")
            elif dataset == "adj_factor":
                frame = self.get_adj_factors(sample[:1], datetime(2018, 1, 1), datetime.now(), "stock")
            elif dataset == "financial":
                frame = self.get_financials("shares", sample[:1])
            elif dataset == "transaction":
                frame = self.get_transactions(sample[:1], _latest_business_day())
            elif dataset == "block":
                frame = self.get_blocks()
            elif dataset == "industry":
                frame = self.get_industries()
            elif dataset == "margin":
                frame = self.get_margin(_latest_business_day())
            elif dataset == "bond":
                rows = self.get_instruments("bond")
                frame = pl.DataFrame(rows[:5]) if rows else pl.DataFrame()
            elif dataset == "realtime":
                records = self.get_realtime(symbols=sample[:1])
                frame = pl.DataFrame(records) if records else pl.DataFrame()
            elif dataset == "full_minute":
                frames = [
                    self.get_minute(sample[:1], datetime.now() - timedelta(days=3), datetime.now(), freq=freq)
                    for freq in ("1m", "5m", "15m", "30m", "60m")
                ]
                frames = [item for item in frames if not item.is_empty()]
                frame = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
            elif dataset == "depth5":
                depths = self.get_depth_batch(sample[:1])
                frame = pl.DataFrame(list(depths.values())) if depths else pl.DataFrame()
            elif dataset == "instruments":
                rows = self.get_instruments("stock")
                frame = pl.DataFrame(rows[:5]) if rows else pl.DataFrame()
            else:
                return {"provider": self.name, "dataset": dataset, "rows": 0, "columns": [], "error": "未支持的数据集"}
        except Exception as exc:  # noqa: BLE001
            return {"provider": self.name, "dataset": dataset, "rows": 0, "columns": [], "error": str(exc)}
        preview = frame.head(3).to_dicts() if not frame.is_empty() else []
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": frame.height,
            "columns": frame.columns,
            "preview": preview,
        }


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# 1e8 万股 = 1 万亿股，A 股不存在。达到这个数量级就是已经换成股。
_SHARES_ALREADY = 100_000_000


def _shares_already_in_shares(*values: Any) -> bool:
    """同一快照里只要有一个字段已经是股，其余字段按同一单位处理。"""
    for value in values:
        number = _float(value)
        if number is not None and number >= _SHARES_ALREADY:
            return True
    return False


def _shares(value: Any, *, already_shares: bool = False) -> float | None:
    """股本统一成股。

    这份 pytdx 解析财务快照时已经把万股乘过 10000，平安银行总股本大约是
    19,405,918,750 股。未换算的旧值仍是万股，例如 20000 表示 2 亿股。
    小于 1 亿的数按万股乘 10000；同一快照里已有股单位时，小的流通股本也不再乘。
    """
    number = _float(value)
    if number is None or number <= 0:
        return None
    if already_shares or number >= _SHARES_ALREADY:
        return number
    return number * 10000.0


def _direction(value: Any) -> str:
    try:
        code = int(value)
    except (TypeError, ValueError):
        return "unknown"
    return {0: "buy", 1: "sell", 2: "auction"}.get(code, "unknown")


def _tick_time(trade_day: date, value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            clock = datetime.strptime(text, fmt).time()
            return datetime.combine(trade_day, clock)
        except ValueError:
            continue
    return None
