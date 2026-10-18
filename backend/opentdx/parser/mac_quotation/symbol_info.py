from datetime import datetime
import struct
from opentdx.const import MARKET, EX_MARKET
from opentdx.parser.baseParser import BaseParser, register_parser


@register_parser(0x122A, 1)
class SymbolInfo(BaseParser):
    def __init__(self, market: MARKET | EX_MARKET, code: str):
        self.is_ex = isinstance(market, EX_MARKET)
        self.body = struct.pack('<H22sI12x', market.value, code.encode('gbk'), 1)

    def deserialize(self, data):
        # data[0:8]: padding (all zeros)
        market, code, name = struct.unpack_from('<H22s44s', data, 8)

        # data[76:96]: padding (all zeros)
        date_raw, time_raw, activity, pre_close, open, high, low, close, momentum, vol, amount, inside_volume, outside_volume = struct.unpack_from('<III5ffIfII', data, 96)
        decimal, a, b, c, vr, turnover, avg = struct.unpack_from('<HIf20xI3f', data, 148)

        return {
            'market': MARKET(market) if not self.is_ex else EX_MARKET(market),
            'code': code.decode("gbk").rstrip('\x00'),
            'name': name.decode("gbk").rstrip('\x00'),
            'time': datetime(year=date_raw // 10000, month=(date_raw % 10000) // 100, day=date_raw % 100, hour=time_raw // 10000, minute=(time_raw % 10000) // 100, second=time_raw % 100),
            'activity': activity,
            'pre_close': pre_close,
            'open': open,
            'high': high,
            'low': low,
            'close': close,
            'momentum': momentum,
            'vol': vol,
            'amount': amount,
            'inside_volume': inside_volume,
            'outside_volume': outside_volume,
            'decimal': decimal,
            'vr': vr,
            'turnover': turnover,
            'avg': avg,
            'extra': [a, b, c],
        }
