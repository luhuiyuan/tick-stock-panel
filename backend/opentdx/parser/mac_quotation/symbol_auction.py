from datetime import time
import struct
from opentdx.const import MARKET, EX_MARKET, PERIOD
from opentdx.parser.baseParser import BaseParser, register_parser


@register_parser(0x123D, 1)
class Auction(BaseParser):
    def __init__(self, market: MARKET | EX_MARKET, code: str, start: int = 0, count: int = 500):
        self.is_ex = isinstance(market, EX_MARKET)
        self.body = struct.pack('<H22sII10x', market.value, code.encode('gbk'), start, count)

    def deserialize(self, data):
        market, code, count = struct.unpack_from('<H22sI', data)
        # data[28:36]: 8 bytes padding (all zeros)

        try:
            market = MARKET(market) if not self.is_ex else EX_MARKET(market)
        except Exception:
            pass

        items = []
        for i in range(count):
            time_sec, price, matched, unmatched = struct.unpack_from('<IfIi', data, 36 + i * 16)
            items.append({
                'time': time(time_sec // 3600, (time_sec % 3600) // 60, time_sec % 60),
                'price': price,
                'matched': matched,
                'unmatched': unmatched
                })

        return {
            'market': market,
            'code': code.decode('gbk', errors='ignore').replace('\x00', ''),
            'items': items
        }
