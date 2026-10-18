import struct
from opentdx.compat import override

from opentdx.parser.baseParser import BaseParser, register_parser

MAX_COUNT = 1000


@register_parser(0x2562, 1)
class GoodsList(BaseParser):
    """扩展市场商品列表 — 返回期货/期权等合约的品种、分类、代码信息"""

    def __init__(self, market: int, start: int = 0, count: int = 600):
        if count > MAX_COUNT:
            raise ValueError(f"count 不能超过 {MAX_COUNT}，当前: {count}")
        self.body = struct.pack('<HII', market, start, count)

    @override
    def deserialize(self, data):
        count, = struct.unpack('<H', data[:2])
        result = []

        for i in range(count):
            offset = 2 + i * 48
            category, name, u, index, switch, v1, v2, v3, c1, c2 = struct.unpack_from(
                '<H23sHIBfffHH', data, offset,
            )
            result.append({
                'name': name.decode('gbk').rstrip('\x00'),
                'category': category,
                'u': u,
                'index': index,
                'switch': switch,
                'code': [v1, v2, v3, c1, c2],
            })

        return result
