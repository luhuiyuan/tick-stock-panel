
from opentdx.const import EX_MARKET, PERIOD
from opentdx.parser.baseParser import BaseParser, register_parser
from opentdx.utils.log import log
import struct
from opentdx.compat import override

@register_parser(0x23f6, 1)
class F23F6(BaseParser):
    def __init__(self):
        self.body = struct.pack('<HHH', 0, 0, 500)

    @override
    def deserialize(self, data):
        start, count = struct.unpack('<IH', data[:6])

        result = []
        for i in range(count):
            z = struct.unpack('<B8sB12H', data[i * 34 + 6: i * 34 + 40])
            log.debug("f23f6 raw: %s", z)

        return None

@register_parser(0x2487, 1)
class F2487(BaseParser):
    def __init__(self, market: EX_MARKET, code: str):
        self.body = struct.pack('<B23s', market.value, code.encode('gbk'))

    @override
    def deserialize(self, data):
        market, code = struct.unpack('<B23s', data[:24])

        active, pre_close, open, high, low, close, u1, price = struct.unpack('<I7f', data[24:56])
        vol, curr_vol, amount = struct.unpack('<IIf', data[56:68])

        # data[68:84]: 4 x uint32 辅助字段
        aux_a, aux_b, aux_c, aux_d = struct.unpack('<4I', data[68:84])
        # data[84:164]: 附加价格/统计 (20 floats, 部分股票为零)
        extra_stats = struct.unpack('<20f', data[84:164])
        # data[164:]: 扩展行情尾段
        tail = struct.unpack('<HII24fB10fHB', data[164:])

        return {
            'market': EX_MARKET(market),
            'code': code.decode('gbk').replace('\x00', ''),
            'active': active,
            'pre_close': pre_close,
            'open': open,
            'high': high,
            'low': low,
            'close': close,
            'price': price,
            'vol': vol,
            'curr_vol': curr_vol,
            'amount': amount,
            'aux': [aux_a, aux_b, aux_c, aux_d],
            'extra_stats': list(extra_stats),
            'tail_fields': list(tail),
        }

# > 8824 22 3030303031300000000000000000000000000000000000 0000 0000 3700 0000000000000000
@register_parser(0x2488, 1) # TODO
class f2488(BaseParser):
    def __init__(self, market: EX_MARKET, code: str):
        self.body = struct.pack('<B23sIHII', market.value, code.encode('gbk'), 0, 55, 0, 0)

    @override
    def deserialize(self, data):
        market, code, count = struct.unpack('<B35sH', data[:38])
        log.debug("f2488 raw: %s %s", EX_MARKET(market), code.decode('gbk').replace('\x00', ''))

        for i in range(count):
            z = struct.unpack('<I6H', data[i * 16 + 38: i * 16 + 54])
            log.debug("f2488 row: %s", z)
        return None

@register_parser(0x2562, 1)
class F2562(BaseParser):
    def __init__(self, market: int, start: int = 0, count: int = 600):
        self.body = struct.pack('<HII', market, start, count)

    @override
    def deserialize(self, data):
        count, = struct.unpack('<H', data[:2])
        result = []

        for i in range(count):
            category, name, market, index, trade_switch, pre_close, u3, u4, u5, u6 = struct.unpack('<H23sHIBfffHH', data[48 * i + 2: 48 * i + 50])
            result.append({
                'name': name.decode('gbk').replace('\x00', ''),
                'category': category,
                'market': market,
                'index': index,
                'trade_switch': trade_switch,
                'pre_close': pre_close,
                'extra': [u3, u4, u5, u6],
            })
        return result
