import struct
from opentdx.compat import override

from opentdx.const import BOARD_TYPE, EX_BOARD_TYPE, EX_MARKET, MARKET
from opentdx.parser.baseParser import BaseParser, register_parser

@register_parser(0x1231, 1)
class BoardList(BaseParser):
    def __init__(self, board_type: BOARD_TYPE | EX_BOARD_TYPE = BOARD_TYPE.ALL, start: int = 0, page_size: int = 150):
        sort_column = 0  # 排序字段? 取不同的值会影响 等于0时候代表rise_speed
        sort_order = 1  # 不确定 sort_column 和 sort_order 具体如何联动
        self.body = struct.pack("<HHBBHH8x", page_size, board_type.value, sort_column, sort_order, start, 1)

    @override
    def deserialize(self, data):
        count_all, total = struct.unpack("<HH", data[:4])
        # 外部传入 page_size , count_all 会是两倍. 这是将 board_info 和 symbol_info 都累加了
        count = count_all // 2

        fmt = "<H6s16s44sfff H6s16s44sfff"
        fmt_length = struct.calcsize(fmt)

        result = []
        for i in range(count):
            market, code, pad1, name, price, rise_speed, pre_close, symbol_market, symbol_code, pad2, symbol_name, symbol_price, symbol_rise_speed, symbol_pre_close = struct.unpack(fmt, data[i * 160 + 4 : i * 160 + 4 + fmt_length])
            result.append({
                "market": MARKET(market) if market <= 3 else EX_MARKET(market),
                "code": code.decode("gbk").replace("\x00", ""),
                "name": name.decode("gbk").replace("\x00", ""),
                "price": price,
                "rise_speed": rise_speed,
                "pre_close": pre_close,
                "symbol_market": MARKET(symbol_market) if symbol_market <= 3 else EX_MARKET(symbol_market),
                "symbol_code": symbol_code.decode("gbk").replace("\x00", ""),
                "symbol_name": symbol_name.decode("gbk").replace("\x00", ""),
                "symbol_price": symbol_price,
                "symbol_rise_speed": symbol_rise_speed,
                "symbol_pre_close": symbol_pre_close,
                "pad1": pad1.hex(),
                "pad2": pad2.hex(),
            })

        return {"total": total, "items": result}
