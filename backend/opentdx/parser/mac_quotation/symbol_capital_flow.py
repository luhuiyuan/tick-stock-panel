import json
import struct
from opentdx.compat import override

from opentdx.const import MARKET
from opentdx.parser.baseParser import BaseParser, register_parser


@register_parser(0x1218, 2)
class SymbolCapitalFlow(BaseParser):
    def __init__(self, symbol: str, market: MARKET):
        self.body = struct.pack("<H8s16x21s", market.value, symbol.encode("gbk"), "Stock_ZJLX".encode("ascii"))

    @override
    def deserialize(self, data):
        market, query_info, ext = struct.unpack("<H12s5x8s", data[:27])

        list_raw = struct.unpack(f"<{len(data) - 27}s", data[27:])[0]
        python_list = json.loads(list_raw.decode("gbk"))

        result = {
            "data": None,
            "query_info": query_info.hex(),
            "ext": ext.hex(),
        }

        if len(python_list) >= 2:
            today_data = python_list[0]
            five_days_data = python_list[1]
            keys = [
                "今日主力流入", "今日主力流出", "今日散户流入", "今日散户流出",
                "5日主买", "5日主卖", "5日超大单净额", "5日大单净额", "5日中单净额", "5日小单净额",
            ]
            merged_data = today_data + five_days_data
            d = dict(zip(keys, merged_data))
            for k in keys:
                try:
                    d[k] = float(d[k])
                except (ValueError, TypeError):
                    pass
            d["今日主力净流入"] = d["今日主力流入"] - d["今日主力流出"]
            d["今日散户净流入"] = d["今日散户流入"] - d["今日散户流出"]
            d["5日主力净流入"] = d["5日主买"] - d["5日主卖"]
            result["data"] = d

        return result
