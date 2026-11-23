"""自定义信号 — 用户用「字段 + 运算符 + 值」组合出的布尔信号。

职责:
  - 从 data/user_data/custom_signals/*.json 加载信号定义
  - 把每个信号的 conditions 编译成一条 Polars 布尔表达式（AND 组合）
  - 供 pipeline 在 compute_signals / compute_enriched_today 末尾注入为列

不知道: 引擎、AI、API、回测、监控。纯函数 + 模块级缓存。

设计:
  - 信号列名加前缀 ``csg_`` 避免与内置 ``signal_`` 列冲突。
  - 回测/策略/监控都按列名找信号，因此注入列后零特殊处理即可三处生效。
  - 字段白名单 + 固定运算符集，杜绝任意表达式注入。
  - 第一版只支持 AND（多条件同时满足）。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import polars as pl

from app.definitions import DefinitionKind, JsonDefinitionStore, register_kind

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────
PREFIX = "csg_"                       # 自定义信号列名前缀
ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
OPS = {">", ">=", "<", "<=", "==", "!="}
# string 扩展字段 (概念/行业归属等) 的运算符: contains 为字面量包含
# (非正则, 用户输入不进入 pattern 编译), ==/!= 为字符串精确比较。
STRING_OPS = {"contains", "==", "!="}
_MAX_STR_RIGHT = 64

# 字段白名单：只允许这些列出现在条件里（防注入）。均为数值型。
# 与 ENRICHED_COLUMNS 的数值列保持一致，排除 symbol/date/name 等非数值列。
# raw_* 为存储列(未复权原始价); limit_*_price 为涨跌停判定价运行时列
# (compute_limit_signals 产出, 已含 0.005 交易所容差)。
ALLOWED_FIELDS: frozenset[str] = frozenset({
    # 行情
    "open", "high", "low", "close", "volume", "amount", "turnover_rate",
    "consecutive_limit_ups", "consecutive_limit_downs",
    # 竞价/开盘量能（由实时 enriched 旁路注入，缺数据时保持 null）
    "gap_pct", "auction_vol_ratio", "opening_vol_ratio_5d",
    # 原始价 (涨跌停类信号判定用)
    "raw_close", "raw_high", "raw_low",
    # 涨跌停判定价 (运行时列: 生效价∓0.005 容差)
    "limit_up_price", "limit_down_price",
    # 基础
    "prev_close", "change_pct", "change_amount", "amplitude",
    # 均线 / 指数均线
    "ma5", "ma10", "ma20", "ma30", "ma60",
    "ema5", "ema10", "ema20", "ema30", "ema60",
    # MACD / BOLL / KDJ / ATR
    "macd_dif", "macd_dea", "macd_hist",
    "boll_upper", "boll_lower",
    "kdj_k", "kdj_d", "kdj_j",
    "atr_14",
    # 量价 / 极值 / 动量 / 波动率 / RSI
    "vol_ma5", "vol_ma10", "vol_ratio_5d",
    "high_60d", "low_60d",
    "momentum_5d", "momentum_10d", "momentum_20d", "momentum_30d", "momentum_60d",
    "annual_vol_20d",
    "rsi_6", "rsi_14", "rsi_24",
    # 异动偏离 (交易所异动规则口径, 运行时列)
    "deviate_3d", "deviate_10d", "deviate_30d",
})

# 运算符 → Polars 表达式构造器（输入 col_expr, value）
_OP_BUILDERS = {
    ">":   lambda c, v: c > v,
    ">=":  lambda c, v: c >= v,
    "<":   lambda c, v: c < v,
    "<=":  lambda c, v: c <= v,
    "==":  lambda c, v: c == v,
    "!=":  lambda c, v: c != v,
    # literal=True: 右值按字面量匹配, 不当正则编译 (用户输入含 .* 等也安全)
    "contains": lambda c, v: c.cast(pl.Utf8).str.contains(v, literal=True),
}


def _string_ext_fields() -> frozenset[str]:
    """string 扩展字段列名 (概念/行业等); 解析/校验按字段 dtype 分发。"""
    try:
        from app.factors.ext_factors import ext_string_fields

        return ext_string_fields()
    except Exception:
        return frozenset()


def allowed_fields() -> frozenset[str]:
    """条件可引用字段 = 物化列白名单 并入 注册表因子 与 string 扩展字段。

    普通信号包含 Alpha158 实验因子; 仅按引用物化, 不扩大默认挖掘目录。
    因子列在历史路径 (compute_signals) 由 materialize_factor_columns 复用
    评分物化管线补算; 盘中单日快照无滚动窗口, 依赖因子的信号被 inject 以
    缺列告警跳过 (与日期偏移条件同样的优雅降级)。
    string 扩展字段 (ext_{表}_{字段}, 概念/行业归属) 只支持 contains/==/!=,
    在帧组装时由 attach_ext_columns 注入, 不注册为因子 (数值口径约束)。
    """
    from app.factors.registry import all_factors

    return frozenset(ALLOWED_FIELDS | {spec.id for spec in all_factors(include_experimental=True)} | _string_ext_fields())


def materialize_factor_columns(
    df: pl.DataFrame,
    exprs: dict[str, pl.Expr],
    needed: set[str] | None = None,
    *,
    trading_dates=None,
) -> pl.DataFrame:
    """把信号表达式引用、且 df 缺失的注册表因子列补算出来。

    复用评分物化路径 (materialize_scoring_columns) — 与检验/评分同一条计算
    逻辑, 不引入第二套实现。非注册表列不在此处理 (缺列仍由 inject 告警跳过)。
    """
    if df.is_empty() or not exprs:
        return df
    from app.strategy.strict_signal_data import MARKET_INDEX, STRICT_FIELDS, attach_calendar

    selected = {n: e for n, e in exprs.items() if needed is None or n in needed}
    roots = set().union(*(e.meta.root_names() for e in selected.values()))
    from app.services.strict_daily_context import prepare_context
    df = prepare_context(df, roots)
    if MARKET_INDEX in roots:
        df = attach_calendar(df, trading_dates)
        # 缺原始列只补 null，绝不伪造数值；使对应严格信号可报告未知覆盖。
        absent = (roots & STRICT_FIELDS) - set(df.columns)
        if absent:
            df = df.with_columns([pl.lit(None, dtype=pl.Float64).alias(f) for f in absent])
    cols = set(df.columns)
    missing: set[str] = set()
    for name, dependencies in expression_dependencies(exprs).items():
        if needed is not None and name not in needed:
            continue
        missing.update(root for root in dependencies if root not in cols)
    if not missing:
        return df
    from app.factors.registry import all_factors

    factor_ids = {spec.id for spec in all_factors(include_experimental=True)}
    if MARKET_INDEX in roots:
        # 按需回测可能只给原始价，虚拟因子的基础指标也必须走既有指标管线。
        from app.factors.registry import factor_dependencies
        from app.indicators.pipeline import INDICATOR_COLUMNS, compute_indicators

        inputs = roots | factor_dependencies(roots & factor_ids)
        indicators = (inputs & INDICATOR_COLUMNS) - set(df.columns)
        if indicators:
            df = compute_indicators(df, needed=indicators)
            missing -= set(df.columns)
    to_compute = missing & factor_ids
    if not to_compute:
        return df
    from app.strategy.scoring import materialize_scoring_columns

    return materialize_scoring_columns(df, sorted(to_compute))


# ── 持久化（统一契约 Store, 镜像写法收口见 app/definitions.py）──
_STORE = JsonDefinitionStore("user_data/custom_signals", label="custom signal")


def _dir(data_dir: Path) -> Path:
    return _STORE.dir(data_dir)


def load_all(data_dir: Path) -> list[dict]:
    """读取全部自定义信号定义。损坏的文件被跳过。"""
    return _STORE.load_all(data_dir)


def load_one(data_dir: Path, signal_id: str) -> dict | None:
    """读取单个信号定义; 不存在或损坏返回 None。"""
    return _STORE.load_one(data_dir, signal_id)


def save_one(data_dir: Path, sig: dict) -> None:
    _STORE.save_one(data_dir, sig)
    invalidate_catalog_cache()


def delete_one(data_dir: Path, signal_id: str) -> bool:
    deleted = _STORE.delete_one(data_dir, signal_id)
    if deleted:
        invalidate_catalog_cache()
    return deleted


# ── 校验 ────────────────────────────────────────────────

MAX_DAYS = 60  # 偏移天数上限 (前N日的 N)


def _parse_days(c: dict, key: str, i: int) -> int:
    """解析并校验条件的天数偏移 (leftDays / rightDays)。返回 0..MAX_DAYS。"""
    raw = c.get(key, 0)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"第 {i+1} 个条件: {key} 必须是整数: {raw!r}")
    if n < 0 or n > MAX_DAYS:
        raise ValueError(f"第 {i+1} 个条件: {key} 必须在 0..{MAX_DAYS} 之间: {n}")
    return n


def _parse_right(right: str, *, string_mode: bool = False) -> tuple[str, object]:
    """解析右值。返回 ('field', colname) / ('const', float) / ('const_str', str)。

    数值模式接受三种形式:
      - 数字 (int / float / 数字字符串) → 常量
      - "field:字段名" → 字段引用
      - 裸字段名 (在白名单内) → 自动视为字段引用
        (AI 生成偶尔漏写 field: 前缀; 白名单字段名不可能是数字, 无歧义)

    string 模式 (左字段是 string 扩展字段): 只接受非空字符串字面量
    (概念/行业名), 不支持字段引用 —— "字段A包含字段B" 无业务语义且
    会与 field: 前缀解析产生歧义。
    """
    if string_mode:
        if not isinstance(right, str) or not right.strip():
            raise ValueError("字符串条件的右值必须是非空字符串 (如概念/行业名)")
        if right.startswith("field:"):
            raise ValueError("字符串条件不支持字段引用右值, 请填字符串字面量")
        if len(right) > _MAX_STR_RIGHT:
            raise ValueError(f"字符串右值过长 (≤{_MAX_STR_RIGHT} 字符): {right[:20]}…")
        return ("const_str", right.strip())
    if isinstance(right, (int, float)):
        return ("const", float(right))
    if not isinstance(right, str):
        raise ValueError(f"非法右值: {right!r}")
    allowed = allowed_fields()
    if right.startswith("field:"):
        col = right[len("field:"):]
        if col not in allowed:
            raise ValueError(f"右值字段不在白名单: {col}")
        return ("field", col)
    # 纯数字
    try:
        return ("const", float(right))
    except ValueError:
        pass
    # 裸字段名 — 兜底容错, 仍受白名单约束
    if right in allowed:
        return ("field", right)
    raise ValueError(f"非法右值（应为 field:xxx 或数字）: {right!r}")


# ── 原子 / 复合信号 ──────────────────────────────────────
# 信号按结构分两层（与 kind=entry/exit/both 交易方向语义正交）:
#   atom（默认）: 现有 113 个信号即原子, conditions 多条件 AND, 不引用其他信号。
#   composite:   不写 conditions, 改写 expr, 用 and/or/not 引用若干原子信号并
#                支持 days 偏移(引用 N 个交易日前该原子的判定)。
# 限制: 复合只引用原子(原子不可再引用), 依赖图天然无环, 无需循环检测。
TYPE_ATOM = "atom"
TYPE_COMPOSITE = "composite"
SIGNAL_TYPES = (TYPE_ATOM, TYPE_COMPOSITE)
EXPR_OPS = {"and", "or", "not"}
MAX_EXPR_DEPTH = 8


def signal_type(sig: dict) -> str:
    """信号结构类型; 缺省 atom (存量信号零改动即为原子)。"""
    return sig.get("type", TYPE_ATOM)


def _validate_expr(expr: object, *, depth: int = 0, path: str = "expr") -> None:
    """校验复合信号 expr 结构。非法抛 ValueError（中文信息）。

    expr 递归定义（JSON）:
      {"ref": "原子信号id", "days": 0..60}           # 引用一个原子
      {"op": "and"|"or", "args": [expr, expr, ...]}  # 至少 2 个参数
      {"op": "not", "args": [expr]}                   # 恰好 1 个参数
    """
    if depth > MAX_EXPR_DEPTH:
        raise ValueError(f"{path} 嵌套过深（≤{MAX_EXPR_DEPTH} 层）")
    if not isinstance(expr, dict):
        raise ValueError(f"{path} 必须是对象")
    if "ref" in expr:
        if "op" in expr or "args" in expr:
            raise ValueError(f"{path} ref 节点不能同时含 op/args")
        ref = expr.get("ref")
        if not isinstance(ref, str) or not ID_RE.match(ref):
            raise ValueError(f"{path} 引用的信号 id 非法: {ref!r}")
        days = expr.get("days", 0)
        try:
            days = int(days)
        except (TypeError, ValueError):
            raise ValueError(f"{path} days 必须是整数") from None
        if days < 0 or days > MAX_DAYS:
            raise ValueError(f"{path} days 必须在 0..{MAX_DAYS} 之间: {days}")
        return
    op = expr.get("op")
    if op not in EXPR_OPS:
        raise ValueError(f"{path} op 必须是 { '/'.join(sorted(EXPR_OPS)) }: {op!r}")
    args = expr.get("args")
    if not isinstance(args, list):
        raise ValueError(f"{path} args 必须是数组")
    if op == "not":
        if len(args) != 1:
            raise ValueError(f"{path} not 需恰好 1 个参数")
    elif len(args) < 2:
        raise ValueError(f"{path} {op} 至少需 2 个参数")
    for i, arg in enumerate(args):
        _validate_expr(arg, depth=depth + 1, path=f"{path}.args[{i}]")


def expr_refs(expr: dict) -> set[str]:
    """收集 expr 里引用的全部原子信号 id。"""
    out: set[str] = set()
    if "ref" in expr:
        out.add(expr["ref"])
        return out
    for arg in expr.get("args") or []:
        if isinstance(arg, dict):
            out |= expr_refs(arg)
    return out


def expr_max_days(expr: dict) -> int:
    """expr 里最大的 days 偏移（用于评估所需历史窗口）。"""
    if "ref" in expr:
        return int(expr.get("days", 0) or 0)
    return max((expr_max_days(a) for a in expr.get("args") or [] if isinstance(a, dict)), default=0)


def validate(sig: dict) -> None:
    """校验一个信号定义，非法则抛 ValueError（含中文信息）。"""
    from app.strategy.strict_signal_data import validate_contract

    validate_contract(sig)
    sid = sig.get("id", "")
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise ValueError(f"信号 id 非法（仅小写字母数字下划线，1-40字符）: {sid!r}")
    explicit_column = sig.get("column")
    if explicit_column is not None and (
        not isinstance(explicit_column, str) or not COLUMN_RE.match(explicit_column)
    ):
        raise ValueError(f"信号 column 非法（仅小写字母数字下划线，1-48字符）: {explicit_column!r}")
    if not isinstance(sig.get("name"), str) or not sig["name"].strip():
        raise ValueError("信号 name 不能为空")
    if sig.get("kind") not in ("entry", "exit", "both"):
        raise ValueError("kind 必须是 entry / exit / both")
    timeframe = sig.get("timeframe", TIMEFRAME_DAILY)
    if timeframe not in (TIMEFRAME_DAILY, TIMEFRAME_INTRADAY):
        raise ValueError(f"timeframe 必须是 {TIMEFRAME_DAILY} / {TIMEFRAME_INTRADAY}: {timeframe!r}")
    stype = signal_type(sig)
    if stype not in SIGNAL_TYPES:
        raise ValueError(f"type 必须是 {'/'.join(SIGNAL_TYPES)}: {stype!r}")
    if stype == TYPE_COMPOSITE:
        # 复合信号: 不写 conditions, 用 expr 引用原子; 仅支持日线。
        if timeframe != TIMEFRAME_DAILY:
            raise ValueError("复合信号仅支持日线 timeframe")
        if sig.get("conditions"):
            raise ValueError("复合信号不能写 conditions, 请用 expr")
        # 复合可显式声明严格数据契约: 未知判定在复合层统一处理, 检查所有引用
        # 原子的原始条件字段。原子保持无契约的纯积木, 便于跨信号复用。
        if sig.get("data_contract"):
            validate_contract(sig)
        _validate_expr(sig.get("expr"))
        return
    conds = sig.get("conditions")
    if not isinstance(conds, list) or len(conds) == 0:
        raise ValueError("conditions 不能为空")
    if len(conds) > 8:
        raise ValueError("conditions 最多 8 条")
    if timeframe == TIMEFRAME_INTRADAY:
        _validate_intraday(sig)
        return
    string_fields = _string_ext_fields()
    for i, c in enumerate(conds):
        if not isinstance(c, dict):
            raise ValueError(f"第 {i+1} 个条件格式错误")
        left = c.get("left", "")
        if left not in allowed_fields():
            raise ValueError(f"第 {i+1} 个条件: 字段 {left!r} 不在白名单")
        is_str = left in string_fields
        if is_str:
            if c.get("op") not in STRING_OPS:
                raise ValueError(
                    f"第 {i+1} 个条件: 字符串字段 {left!r} 仅支持 "
                    f"{'/'.join(sorted(STRING_OPS))} 运算符"
                )
        elif c.get("op") == "contains":
            raise ValueError(f"第 {i+1} 个条件: contains 仅用于字符串扩展字段")
        elif c.get("op") not in OPS:
            raise ValueError(f"第 {i+1} 个条件: 运算符 {c.get('op')!r} 非法")
        _parse_right(c.get("right"), string_mode=is_str)   # 会校验右值字段/数字/字符串
        _parse_days(c, "leftDays", i)   # 左字段偏移
        _parse_days(c, "rightDays", i)  # 右字段偏移


# ── 编译为 Polars 表达式 ─────────────────────────────────
def column_name(signal_id: str) -> str:
    """信号 id → DataFrame 列名（加前缀）。"""
    return f"{PREFIX}{signal_id}"


COLUMN_RE = re.compile(r"^[a-z0-9_]{1,48}$")


def resolve_column(sig: dict) -> str:
    """信号定义 → 输出列名。

    默认 ``csg_{id}``; 定义可携带显式 ``column`` 字段改写列名 (如迁移自
    内置信号的定义沿用 ``signal_*`` 原名, 下游消费方零改动)。显式列名
    仅允许小写字母数字下划线, 防止任意列名注入。
    """
    explicit = sig.get("column")
    if explicit:
        if not isinstance(explicit, str) or not COLUMN_RE.match(explicit):
            raise ValueError(f"信号 column 非法（仅小写字母数字下划线，1-48字符）: {explicit!r}")
        return explicit
    return column_name(sig["id"])


def _col(name: str, days: int = 0) -> pl.Expr:
    """构造列表达式; days>0 时取 N 个交易日前的值 (按 symbol 分组 shift)。"""
    expr = pl.col(name)
    if days > 0:
        expr = expr.shift(days).over("symbol")
    return expr


# ── 叠加条件 (策略 overlay_filter) ──────────────────────
# 每策略「叠加条件」: {left, op, right, leftDays?, rightDays?} 条件列表,
# AND 组合的硬过滤, 直接叠加在策略自身规则之上 (策略/回测/监控三处一致),
# 不落信号 JSON。条件结构/白名单/运算符/日期偏移与自定义信号完全同源:
# leftDays/rightDays 为按交易日回看的偏移 (0..MAX_DAYS, 按 symbol 分组 shift)。
_OVERLAY_MAX_CONDITIONS = 8


def validate_overlay_conditions(conditions: object) -> None:
    """校验叠加条件列表 (策略 override 的 overlay_filter 键)。非法抛 ValueError。

    空列表合法 (= 清空叠加条件)。字段白名单与运算符同自定义信号
    (allowed_fields 含注册表因子与字符串扩展字段); 日期偏移同源支持
    (leftDays/rightDays, 0..MAX_DAYS)。
    """
    if not isinstance(conditions, list):
        raise ValueError("overlay_filter 必须是条件数组")
    if not conditions:
        return
    if len(conditions) > _OVERLAY_MAX_CONDITIONS:
        raise ValueError(f"叠加条件最多 {_OVERLAY_MAX_CONDITIONS} 条")
    string_fields = _string_ext_fields()
    for i, c in enumerate(conditions):
        if not isinstance(c, dict):
            raise ValueError(f"第 {i+1} 个叠加条件格式错误")
        left = c.get("left", "")
        if left not in allowed_fields():
            raise ValueError(f"第 {i+1} 个叠加条件: 字段 {left!r} 不在白名单")
        is_str = left in string_fields
        if is_str:
            if c.get("op") not in STRING_OPS:
                raise ValueError(
                    f"第 {i+1} 个叠加条件: 字符串字段 {left!r} 仅支持 "
                    f"{'/'.join(sorted(STRING_OPS))} 运算符"
                )
        elif c.get("op") not in OPS:
            raise ValueError(f"第 {i+1} 个叠加条件: 运算符 {c.get('op')!r} 非法")
        _parse_right(c.get("right"), string_mode=is_str)
        _parse_days(c, "leftDays", i)   # 左字段偏移
        _parse_days(c, "rightDays", i)  # 右字段偏移


def overlay_max_days(conditions: object) -> int:
    """叠加条件里最大的日期偏移 (0 = 无偏移)。调用方据此评估所需历史窗口。"""
    if not isinstance(conditions, list):
        return 0
    max_days = 0
    for c in conditions:
        if not isinstance(c, dict):
            continue
        for key in ("leftDays", "rightDays"):
            try:
                days = int(c.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
            if days > max_days:
                max_days = days
    return max_days


def build_overlay_expr(conditions: list[dict]) -> pl.Expr | None:
    """把叠加条件编译成一条 AND 组合的布尔表达式 (无 csg_ 前缀, 不落盘)。

    与 build_expressions 同一套编译件 (_OP_BUILDERS/_parse_right/_col);
    日期偏移 (leftDays/rightDays) 与自定义信号同口径, 按 symbol 分组 shift。
    条件已被 validate_overlay_conditions 拦过一遍, 这里编译失败直接抛
    ValueError (fail-closed, 不静默跳过 — 静默跳过等于悄悄放宽过滤,
    与硬过滤语义冲突)。
    null 语义: 表达式不做 fill, 由调用方统一 fill_null(False)
    (缺数据日不入选)。
    """
    if not conditions:
        return None
    string_fields = _string_ext_fields()
    parts: list[pl.Expr] = []
    for c in conditions:
        left, op = c["left"], c["op"]
        is_str = left in string_fields
        kind, val = _parse_right(c["right"], string_mode=is_str)
        left_days = int(c.get("leftDays", 0) or 0)
        right_days = int(c.get("rightDays", 0) or 0)
        right_expr = _col(val, right_days) if kind == "field" else val
        parts.append(_OP_BUILDERS[op](_col(left, left_days), right_expr))
    combined = parts[0]
    for p in parts[1:]:
        combined = combined & p
    return combined


def overlay_missing_columns(conditions: list[dict], columns) -> list[str]:
    """叠加条件引用、且不在 columns 里的根字段 (供 fail-closed 检查)。"""
    if not conditions:
        return []
    missing: list[str] = []
    have = set(columns)
    for c in conditions:
        left = str(c.get("left", ""))
        if left and left not in have and left not in missing:
            missing.append(left)
        right = c.get("right")
        if isinstance(right, str) and right.startswith("field:"):
            col = right[len("field:"):]
            if col and col not in have and col not in missing:
                missing.append(col)
    return missing



def _compile_atom_expr(sig: dict, allow_shift: bool = True,
                       eps_contract: dict | None = None) -> pl.Expr:
    """把一个原子信号的 conditions 编译成布尔表达式（沿用原有逻辑）。

    eps_contract: 复合信号内联本原子时传入复合自身的契约, 用于决定是否对
    >=/<= 边界比较加 8-ULP 容差。这样带契约复合内联「无契约纯积木」原子时,
    仍保留与原单信号完全一致的数值判定语义。guard 不在此处理（由复合层统一
    判定）, 因此该参数只影响边界容差, 不引入 guard 漂移。
    """
    conds = sig["conditions"]
    # eps 来源: 显式传入的复合契约优先, 否则用原子自身契约
    eps_on = eps_contract if eps_contract is not None else sig.get("data_contract")
    string_fields = _string_ext_fields()
    operands: list[pl.Expr] = []
    parts: list[pl.Expr] = []
    for c in conds:
        left_days = int(c.get("leftDays", 0) or 0)
        right_days = int(c.get("rightDays", 0) or 0)
        # 盘中路径不支持偏移 → 跳过整个信号
        if not allow_shift and (left_days > 0 or right_days > 0):
            raise ValueError("盘中实时路径不支持日期偏移条件, 已跳过")
        left = c["left"]
        op = c["op"]
        is_str = left in string_fields
        if is_str and op not in STRING_OPS:
            raise ValueError(f"字符串字段 {left!r} 不支持运算符 {op!r}")
        if op == "contains" and not is_str:
            raise ValueError(f"contains 仅用于字符串扩展字段: {left!r}")
        kind, val = _parse_right(c["right"], string_mode=is_str)
        right_expr = _col(val, right_days) if kind == "field" else val
        left_expr = _col(left, left_days)
        operands.append(left_expr)
        if kind == "field":
            operands.append(right_expr)
        # 比率在阈值相等处会有二进制舍入误差；仅严格契约的非严格比较
        # 使用 8 ULP 量级容差，严格 >/< 不放宽。
        if eps_on and op in (">=", "<="):
            rhs = right_expr if kind == "field" else pl.lit(right_expr)
            eps = 8 * 2.220446049250313e-16 * pl.max_horizontal(left_expr.abs(), rhs.abs(), pl.lit(1.0))
            parts.append(left_expr >= rhs - eps if op == ">=" else left_expr <= rhs + eps)
        else:
            parts.append(_OP_BUILDERS[op](left_expr, right_expr))
    combined = parts[0]
    for p in parts[1:]:
        combined = combined & p
    from app.strategy.strict_signal_data import guard

    return guard(sig, combined, operands)


def _signal_catalog() -> dict[str, dict]:
    """全部信号定义 {id: sig}; 供复合信号解析引用。带模块级缓存。"""
    global _catalog_cache
    if _catalog_cache is None:
        try:
            from app.config import settings

            _catalog_cache = {s["id"]: s for s in load_all(settings.data_dir) if s.get("id")}
        except Exception as e:
            logger.warning("composite signal catalog load failed: %s", e)
            _catalog_cache = {}
    return _catalog_cache


_catalog_cache: dict[str, dict] | None = None


def invalidate_catalog_cache() -> None:
    """信号定义增删后调用, 使复合引用的 catalog 缓存失效。"""
    global _catalog_cache
    _catalog_cache = None


def _compile_expr(expr: dict, catalog: dict[str, dict], allow_shift: bool = True,
                  _depth: int = 0, eps_contract: dict | None = None) -> pl.Expr:
    """递归编译复合 expr; 引用的原子被内联为子表达式。

    days 偏移通过对原子表达式整体 ``shift(days).over('symbol')`` 实现。
    未知传播: and/or/not 用 Kleene 三值逻辑(Boolean) —— 原子未知(null)时
    结果自然为 null, 满足"任一原子 unknown → 复合 unknown"。
    """
    if _depth > MAX_EXPR_DEPTH:
        raise ValueError(f"expr 嵌套过深（≤{MAX_EXPR_DEPTH} 层）")
    if "ref" in expr:
        ref = expr["ref"]
        atom = catalog.get(ref)
        if atom is None:
            raise ValueError(f"复合信号引用的原子不存在: {ref}")
        if signal_type(atom) != TYPE_ATOM:
            raise ValueError(f"复合信号只能引用原子信号: {ref} 是 {signal_type(atom)}")
        days = int(expr.get("days", 0) or 0)
        if not allow_shift and days > 0:
            raise ValueError("盘中实时路径不支持复合引用的 days 偏移")
        sub = _compile_atom_expr(atom, allow_shift=allow_shift, eps_contract=eps_contract)
        return sub.shift(days).over("symbol") if days > 0 else sub
    op = expr["op"]
    args = [_compile_expr(a, catalog, allow_shift=allow_shift, _depth=_depth + 1,
                          eps_contract=eps_contract)
            for a in expr["args"]]
    if op == "not":
        return ~args[0]
    if op == "and":
        out = args[0]
        for a in args[1:]:
            out = out & a
        return out
    out = args[0]
    for a in args[1:]:
        out = out | a
    return out


def _composite_guard_operands(expr: dict, catalog: dict[str, dict]) -> list[pl.Expr]:
    """收集复合 expr 引用的所有原子的原始条件字段（相对当日, 不平移）。

    复合信号的严格数据契约在复合层统一判定: guard 需要检查所有引用原子的
    条件字段是否完整, 且必须用「原始字段 + 原始 leftDays/rightDays」(而非
    expr 里被 ref+days 平移后的形态), 才能与原单原子信号的 unknown 语义严格
    等价。字段不去重 (与原子 guard 的 operands 口径一致, is_finite 幂等)。
    """
    operands: list[pl.Expr] = []
    for ref in sorted(expr_refs(expr)):
        atom = catalog.get(ref)
        if not atom:
            continue
        for c in atom.get("conditions") or []:
            if not isinstance(c, dict):
                continue
            operands.append(_col(c["left"], int(c.get("leftDays", 0) or 0)))
            kind, val = _parse_right(c["right"])
            if kind == "field":
                operands.append(_col(val, int(c.get("rightDays", 0) or 0)))
    return operands


def build_expressions(signals: list[dict], allow_shift: bool = True,
                      catalog: dict[str, dict] | None = None) -> dict[str, pl.Expr]:
    """把多个自定义信号编译成 {column_name: pl.Expr}。

    - 只处理 enabled != False 的信号。
    - 原子信号: 多条件用 ``&`` 串联（AND）。
    - 复合信号: expr 递归内联引用的原子（and/or/not + days 偏移）。
    - allow_shift=False 时, 跳过带日期偏移 (leftDays/rightDays/days>0) 的信号。
    - 编译失败的信号被跳过并告警（不影响其它信号）。
    - catalog: id→sig 映射; 缺省时从信号目录懒加载（供复合解析引用）。
    """
    out: dict[str, pl.Expr] = {}
    for sig in signals:
        if sig.get("enabled") is False:
            continue
        try:
            col_name = resolve_column(sig)
            stype = signal_type(sig)
            if stype == TYPE_COMPOSITE:
                if sig.get("data_contract") and not allow_shift:
                    out[col_name] = pl.lit(None, dtype=pl.Boolean)
                    continue
                cat = catalog if catalog is not None else _signal_catalog()
                compiled = _compile_expr(sig["expr"], cat, allow_shift=allow_shift,
                                         eps_contract=sig.get("data_contract"))
                # 复合自带严格契约时, 未知判定在复合层统一处理: 收集所有引用
                # 原子的原始条件字段做完整性 guard。这样原子只需做纯条件积木,
                # 复合的 unknown 语义与等价的单原子信号严格一致 (guard 不会
                # 随 ref+days 平移而层层加严)。
                if sig.get("data_contract"):
                    from app.strategy.strict_signal_data import guard as _guard
                    operands = _composite_guard_operands(sig["expr"], cat)
                    compiled = _guard(sig, compiled, operands)
                out[col_name] = compiled
                continue
            # 原子信号（原有路径）
            if sig.get("data_contract") and not allow_shift:
                out[col_name] = pl.lit(None, dtype=pl.Boolean)
                continue
            out[col_name] = _compile_atom_expr(sig, allow_shift=allow_shift)
        except Exception as e:
            logger.warning("custom signal compile failed %s: %s", sig.get("id"), e)
    return out


def prev_day_fields(signals: list[dict]) -> set[str]:
    """全部定义里以 leftDays/rightDays=1 引用的根字段集合。

    供当日(实时)路径预 join 昨日值: compute_enriched_today 据此 select
    ``_prev_{field}`` 列, build_expressions_prev 再把它们映射进表达式。
    偏移 >1 的字段当日路径无解 (需多日历史), 不在此列。
    """
    fields: set[str] = set()
    for sig in signals:
        for c in sig.get("conditions") or []:
            if not isinstance(c, dict):
                continue
            if int(c.get("leftDays", 0) or 0) == 1 and isinstance(c.get("left"), str):
                fields.add(c["left"])
            right = c.get("right")
            if (
                int(c.get("rightDays", 0) or 0) == 1
                and isinstance(right, str)
                and right.startswith("field:")
            ):
                fields.add(right[len("field:"):])
    return fields


def build_expressions_prev(
    signals: list[dict],
    prev_available: set[str],
) -> dict[str, pl.Expr]:
    """当日(实时)路径的表达式构建: 偏移=1 的字段改读 ``_prev_{field}`` join 列。

    - 昨日值列缺失 (prev_available 不含该字段) 或偏移 >1 的信号被跳过并告警
      (单日快照无法回看多日; 与旧内置行为的差异在于昨日值由 join 提供)。
    - 其余编译逻辑与 build_expressions 完全同源。
    """
    def _col_prev(name: str, days: int) -> pl.Expr:
        if days == 0:
            return pl.col(name)
        if days == 1:
            return pl.col(f"_prev_{name}")
        raise ValueError(f"当日路径不支持 {days} 日偏移: {name}")

    out: dict[str, pl.Expr] = {}
    string_fields = _string_ext_fields()
    for sig in signals:
        if sig.get("enabled") is False:
            continue
        try:
            col_name = resolve_column(sig)
            if sig.get("data_contract"):
                out[col_name] = pl.lit(None, dtype=pl.Boolean)
                continue
            parts: list[pl.Expr] = []
            for c in sig["conditions"]:
                left_days = int(c.get("leftDays", 0) or 0)
                right_days = int(c.get("rightDays", 0) or 0)
                if left_days > 1 or right_days > 1:
                    raise ValueError("当日实时路径不支持 >1 日偏移, 已跳过")
                left = c["left"]
                op = c["op"]
                is_str = left in string_fields
                kind, val = _parse_right(c["right"], string_mode=is_str)
                # 偏移字段须已由调用方 join 出 _prev_ 列
                for name, days in ((left, left_days), (val if kind == "field" else None, right_days)):
                    if name is None:
                        continue
                    if days == 1 and name not in prev_available:
                        raise ValueError(f"当日路径缺少昨日值列: _prev_{name}")
                right_expr = _col_prev(val, right_days) if kind == "field" else val
                parts.append(_OP_BUILDERS[op](_col_prev(left, left_days), right_expr))
            combined = parts[0]
            for p in parts[1:]:
                combined = combined & p
            out[col_name] = combined
        except Exception as e:
            logger.warning("custom signal (today) compile failed %s: %s", sig.get("id"), e)
    return out


def expression_dependencies(exprs: dict[str, pl.Expr] | None = None) -> dict[str, frozenset[str]]:
    """返回自定义信号列到根字段的依赖映射。"""
    source = exprs if exprs is not None else {}
    from app.strategy.strict_signal_data import MARKET_INDEX

    return {name: frozenset(_expr_root_columns(expr)) - {MARKET_INDEX} for name, expr in source.items()}


def inject(
    df: pl.DataFrame,
    exprs: dict[str, pl.Expr],
    needed: set[str] | None = None,
) -> pl.DataFrame:
    """把编译好的信号表达式作为列加入 df。

    ``needed=None`` 保持历史全量语义；传入集合时只注入被请求的自定义信号。
    缺失依赖会明确告警，避免回测静默丢失信号。
    """
    if df.is_empty() or not exprs:
        return df
    cols = set(df.columns)
    add: dict[str, pl.Expr] = {}
    for name, expr in exprs.items():
        if needed is not None and name not in needed:
            continue
        # 提取该表达式引用的所有字段列，缺失则跳过（避免运行时报错）
        required = _expr_root_columns(expr)
        if required.issubset(cols):
            add[name] = expr
        else:
            from app.strategy.strict_signal_data import MARKET_INDEX

            if MARKET_INDEX in required:
                add[name] = pl.lit(None, dtype=pl.Boolean)
            logger.warning(
                "custom signal %s missing dependencies: %s",
                name,
                sorted(required - cols),
            )
    if add:
        df = df.with_columns([e.alias(n) for n, e in add.items()])
    return df


def _expr_root_columns(expr: pl.Expr) -> set[str]:
    """尽力提取表达式里出现的列名。失败则返回空集（保守跳过）。"""
    try:
        # Polars 的 meta.root_names() 返回表达式引用的根列名
        names = expr.meta.root_names()
        return set(names)
    except Exception:
        return set()


# ══ 盘中信号(timeframe="intraday")═════════════════════════
# 与日线自定义信号同一套 left/op/right 条件结构, 但:
#   - 字段白名单换成分钟特征(intraday_features.INTRADAY_FEATURES);
#   - 运算符额外支持 cross_up / cross_down(序列上穿/下穿 另一序列或阈值);
#   - 不支持 leftDays/rightDays 日期偏移;
#   - 信号列名前缀 csgi_, 注入对象是分钟特征帧而非日线 enriched。
# 语义: 信号输出 = 当日条件组合的上升沿(false→true), 首根 bar 不触发。

from app.strategy.intraday_features import INTRADAY_FEATURES  # noqa: E402

TIMEFRAME_DAILY = "daily"
TIMEFRAME_INTRADAY = "intraday"
INTRADAY_PREFIX = "csgi_"
INTRADAY_OPS = OPS | {"cross_up", "cross_down"}
_EDGE_GROUP = ["symbol", "date"]


def intraday_column_name(signal_id: str) -> str:
    """盘中信号 id → 分钟帧列名(加 csgi_ 前缀)。"""
    return f"{INTRADAY_PREFIX}{signal_id}"


def _parse_right_intraday(right: object) -> tuple[str, object]:
    """盘中条件的右值: ('const', float) 或 ('field', 特征名)。"""
    if isinstance(right, (int, float)):
        return ("const", float(right))
    if not isinstance(right, str):
        raise ValueError(f"非法右值: {right!r}")
    if right.startswith("field:"):
        col = right[len("field:"):]
        if col not in INTRADAY_FEATURES:
            raise ValueError(f"盘中右值字段不在白名单: {col}")
        return ("field", col)
    try:
        return ("const", float(right))
    except ValueError:
        pass
    if right in INTRADAY_FEATURES:
        return ("field", right)
    raise ValueError(f"非法盘中右值(应为 field:特征 或数字): {right!r}")


def _validate_intraday(sig: dict) -> None:
    """校验盘中信号定义, 非法抛 ValueError。"""
    conds = sig.get("conditions")
    for i, c in enumerate(conds):
        if not isinstance(c, dict):
            raise ValueError(f"第 {i+1} 个条件格式错误")
        left = c.get("left", "")
        if left not in INTRADAY_FEATURES:
            raise ValueError(f"第 {i+1} 个条件: 盘中字段 {left!r} 不在白名单")
        if c.get("op") not in INTRADAY_OPS:
            raise ValueError(f"第 {i+1} 个条件: 运算符 {c.get('op')!r} 非法(盘中额外支持 cross_up/cross_down)")
        _parse_right_intraday(c.get("right"))
        if int(c.get("leftDays", 0) or 0) or int(c.get("rightDays", 0) or 0):
            raise ValueError(f"第 {i+1} 个条件: 盘中信号不支持日期偏移(leftDays/rightDays)")
    min_bars = sig.get("min_bars", 0)
    try:
        n = int(min_bars)
    except (TypeError, ValueError):
        raise ValueError(f"min_bars 必须是整数: {min_bars!r}")  # noqa: B904
    if n < 0 or n > 240:
        raise ValueError(f"min_bars 必须在 0..240 之间: {n}")


def build_intraday_expressions(signals: list[dict]) -> dict[str, pl.Expr]:
    """把盘中信号编译为特征帧上的「条件」表达式(AND 组合, 未做上升沿)。

    表达式在 intraday_features.build_feature_frame 产出的帧上求值;
    上升沿须通过 apply_intraday_edges 在 DataFrame 层两步计算 —
    对已含 .over() 窗口的组合表达式直接 shift().over() 是窗口嵌套,
    Polars 会返回全 null。编译失败的信号跳过并告警。
    """
    out: dict[str, pl.Expr] = {}
    for sig in signals:
        if sig.get("enabled") is False or sig.get("timeframe") != TIMEFRAME_INTRADAY:
            continue
        try:
            parts: list[pl.Expr] = []
            for c in sig["conditions"]:
                left = pl.col(c["left"])
                kind, val = _parse_right_intraday(c["right"])
                op = c["op"]
                if op == "cross_up":
                    # 前一根 bar 未满足 且 当前 bar 满足; 右值为常量时不 shift 字面量
                    if kind == "field":
                        prev_ok = left.shift(1).over(_EDGE_GROUP) <= pl.col(val).shift(1).over(_EDGE_GROUP)
                        cur_ok = left > pl.col(val)
                    else:
                        prev_ok = left.shift(1).over(_EDGE_GROUP) <= val
                        cur_ok = left > val
                    parts.append(prev_ok & cur_ok)
                elif op == "cross_down":
                    if kind == "field":
                        prev_ok = left.shift(1).over(_EDGE_GROUP) >= pl.col(val).shift(1).over(_EDGE_GROUP)
                        cur_ok = left < pl.col(val)
                    else:
                        prev_ok = left.shift(1).over(_EDGE_GROUP) >= val
                        cur_ok = left < val
                    parts.append(prev_ok & cur_ok)
                else:
                    right = pl.col(val) if kind == "field" else val
                    parts.append(_OP_BUILDERS[op](left, right))
            combined = parts[0]
            for p in parts[1:]:
                combined = combined & p
            out[intraday_column_name(sig["id"])] = combined
        except Exception as e:
            logger.warning("intraday signal compile failed %s: %s", sig.get("id"), e)
    return out


def apply_intraday_edges(frame: pl.DataFrame, exprs: dict[str, pl.Expr]) -> pl.DataFrame:
    """对特征帧求值盘中信号: 先算条件列, 再取「当日条件上升沿」为布尔列。

    上升沿: 条件 false→true 的那根 bar 为 true; 首根 bar(前值为 null)不触发;
    条件含 null(特征不足)视为 false。四条消费路径(监控/实盘/回测/回放)
    必须共用本函数, 保证口径一致。
    """
    if frame.is_empty() or not exprs:
        return frame
    df = frame.with_columns([e.fill_null(False).alias(n) for n, e in exprs.items()])
    return df.with_columns([
        (
            pl.col(n)
            & ~pl.col(n).shift(1).over(_EDGE_GROUP).fill_null(True)
        ).cast(pl.Boolean).alias(n)
        for n in exprs
    ])


# ── 盘中信号定义加载(带指纹缓存: 引擎/监控高频路径用) ──────────
_intraday_cache: dict[Path, tuple[object, list[dict]]] = {}


def load_intraday_all(data_dir: Path) -> list[dict]:
    """读取全部启用的盘中信号定义(带缓存)。

    盘中评估与引擎注入每分钟执行, 不宜每次全量读盘; save/delete 端点
    调用 invalidate_intraday_cache() 主动失效。
    """
    fp = _STORE.fingerprint(data_dir)
    cached = _intraday_cache.get(data_dir)
    if cached is not None and cached[0] == fp:
        return cached[1]
    sigs = [
        s for s in load_all(data_dir)
        if s.get("timeframe") == TIMEFRAME_INTRADAY and s.get("enabled") is not False
    ]
    _intraday_cache[data_dir] = (fp, sigs)
    return sigs


def invalidate_intraday_cache() -> None:
    _intraday_cache.clear()


# ── 信号命名映射 (告警文案把 csg_/csgi_ 列名翻译成用户命名) ──────────
_names_cache: dict[Path, tuple[object, dict[str, str]]] = {}


def seed_if_empty(data_dir: Path) -> int:
    """去内置化迁移: 首次运行种入 20 个默认信号定义 (与已有用户定义共存)。

    - 幂等: ``.seeded_v1`` 标记存在 (无论目录后来是否被用户清空) 不再种入,
      用户删除全部信号后重启不会"复活"。
    - 仅写目录中缺失的 id, 已有文件一律不覆盖: 旧环境整体拷贝 (已含全部
      种子) 时零写入; 仅有旧版自定义信号的升级用户也能拿到默认信号。
      (曾因「目录非空即整体跳过」令升级用户丢失内置涨跌停信号,
      表现为看板跌停恒 0、连板梯队无数据。)
    - 历史遗留: 旧逻辑写过 ``skipped: user signals present`` 标记, 该标记
      同样视为未种子化, 升级后自动补种缺失项。
    """
    from app.strategy.signal_seeds import SEED_SIGNALS

    d = _dir(data_dir)
    marker = d / ".seeded_v1"
    if marker.exists():
        try:
            if "skipped" not in marker.read_text(encoding="utf-8"):
                return 0
        except OSError:
            return 0
    existing = {p.stem for p in d.glob("*.json")}
    written = 0
    for sig in SEED_SIGNALS:
        if sig["id"] in existing:
            continue
        save_one(data_dir, sig)
        written += 1
    marker.write_text(f"seeded: {written}\n", encoding="utf-8")
    return written


def signal_names(data_dir: Path) -> dict[str, str]:
    """自定义信号列名 (csg_/csgi_) → 用户命名的映射, 带目录指纹缓存。

    指纹含文件名 + mtime, 保存/删除信号后自动失效重载, 调用方无需配合失效。
    """
    fp = _STORE.fingerprint(data_dir)
    cached = _names_cache.get(data_dir)
    if cached is not None and cached[0] == fp:
        return cached[1]
    names: dict[str, str] = {}
    for s in load_all(data_dir):
        sid, name = s.get("id"), s.get("name")
        if sid and name:
            # 显式 column (迁移自内置的 signal_*) 优先于默认 csg_ 前缀
            names[resolve_column(s)] = name
            names[intraday_column_name(sid)] = name
    _names_cache[data_dir] = (fp, names)
    return names


# ── 统一定义契约注册 (P1b) ───────────────────────────────
def _definition_dependencies(sig: dict) -> frozenset[str]:
    """信号定义 → 输入列 (统一契约的依赖提取器)。

    复用编译路径本身 (build_expressions / build_intraday_expressions →
    meta.root_names), 与回测/管线看到的依赖同源, 不引入第二套提取。
    编译失败 (build_* 已告警) 返回空集 — 上游本就按缺列跳过该信号。
    """
    exprs = (
        build_intraday_expressions([sig])
        if sig.get("timeframe") == TIMEFRAME_INTRADAY
        else build_expressions([sig], catalog=_signal_catalog())
    )
    if not exprs:
        return frozenset()
    return next(iter(expression_dependencies(exprs).values()))


register_kind(DefinitionKind(
    kind="signal",
    store=_STORE,
    id_pattern=ID_RE,
    validate=validate,
    dependencies=_definition_dependencies,
))
