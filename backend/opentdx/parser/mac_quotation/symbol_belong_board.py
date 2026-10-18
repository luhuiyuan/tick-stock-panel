import json
import struct

from opentdx.compat import override

from opentdx.const import MARKET
from opentdx.parser.baseParser import BaseParser, register_parser


@register_parser(0x1218, 1)
class SymbolBelongBoard(BaseParser):
    def __init__(self, symbol: str, market: MARKET):
        self.body = struct.pack("<H8s16x21s", market.value, symbol.encode("gbk"), "Stock_GLHQ".encode("ascii"))

        # pkg = bytearray.fromhex('0000 0000 0000 0000 \
        # 0000 0000 0000 0000 0000 5374 6f63 6b5f \
        # 474c 4851 0000 0000 0000 0000 0000 00   ')


    @override
    def deserialize(self, data):
        market, query_info, ext = struct.unpack("<H12s5x8s", data[:27])

        list_raw = struct.unpack(f"<{len(data) - 27}s", data[27:])[0]
        python_list = json.loads(list_raw.decode("gbk"))

        result = {
            "data": [],
            "query_info": query_info.hex(),
            "ext": ext.hex(),
        }

        if python_list:
            first_row = python_list[0]
            n = len(first_row)

            if n == 9:
                keys = ["board_type", "market", "board_symbol", "board_symbol_name", "close", "pre_close", "涨停数", "跌停数", "最相似"]
            elif n == 13:
                keys = ["board_type", "market", "board_symbol", "board_symbol_name", "close", "pre_close",
                        "speed_pct", "symbol_market", "symbol", "symbol_name", "symbol_close", "symbol_pre_close", "symbol_speed_pct"]
            else:
                keys = None

            if keys:
                for row in python_list:
                    d = dict(zip(keys, row))
                    for col in ("close", "pre_close"):
                        if col in d:
                            try:
                                d[col] = float(d[col])
                            except (ValueError, TypeError):
                                pass
                    result["data"].append(d)
            else:
                result["data"] = python_list

        return result
