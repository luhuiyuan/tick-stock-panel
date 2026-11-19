"""固定历史窗口数值特征；由既有注册表/评分管线按需调用，无IO。"""

from __future__ import annotations

import polars as pl


def value_expr(name: str) -> pl.Expr | None:
    h, low, c, o, a = (pl.col(x) for x in ("high", "low", "close", "open", "amount"))
    if name in {
        "history_gap_support_count19",
        "history_double_amount_support_count19",
        "history_peak_amount_support_count19",
    }:
        conditions = []
        for k in range(1, 20):
            after_low = low if k == 1 else low.rolling_min(k, min_samples=k)
            if name == "history_gap_support_count19":
                hit = (low.shift(k) > h.shift(k + 1)) & (after_low > h.shift(k + 1))
            else:
                hit = (c.shift(k) > o.shift(k)) & (after_low > low.shift(k))
                if name == "history_double_amount_support_count19":
                    hit &= a.shift(k) / a.shift(k + 1) >= 2
                else:
                    hit &= a.shift(k) >= a.shift(1).rolling_max(20, min_samples=20)
            conditions.append(hit.cast(pl.Float64))
        # Python加法构造固定表达式，不逐行回调；null不能被水平sum跳过。
        result = sum(conditions).over("symbol")
        if name == "history_double_amount_support_count19":
            valid_denominators = (a.shift(2).rolling_min(19, min_samples=19) > 0).over("symbol")
            result = pl.when(valid_denominators).then(result).otherwise(None)
        return result
    if name in {"range_high_touch_count15", "range_low_touch_count15"}:
        if name == "range_high_touch_count15":
            edge = h.rolling_max(15, min_samples=15) * 0.99
            terms = [(h.shift(k) >= edge).cast(pl.Float64) for k in range(15)]
        else:
            edge = low.rolling_min(15, min_samples=15) * 1.01
            terms = [(low.shift(k) <= edge).cast(pl.Float64) for k in range(15)]
        return sum(terms).over("symbol")
    return None
