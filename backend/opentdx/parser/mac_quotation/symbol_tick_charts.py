from datetime import date, datetime, time
import struct
from opentdx.const import MARKET, EX_MARKET
from opentdx.parser.baseParser import BaseParser, register_parser


@register_parser(0x123E, 1)
class TickCharts(BaseParser):
    def __init__(self, market: MARKET | EX_MARKET, code: str, query_date: date = None, days: int = 5):
        self.is_ex = isinstance(market, EX_MARKET)
        start_day = query_date.year * 10000 + query_date.month * 100 + query_date.day if query_date else 0
        self.body = struct.pack('<H22sIHH6x', market.value, code.encode('gbk'), start_day, days, 1)

    def deserialize(self, data):
        market, code = struct.unpack_from('<H22s', data, 0)

        pre_closes = struct.unpack_from('<5I5f', data, 24)
                #透传最后那个值，没看出意义来
        count, send_last, page_size, total = struct.unpack_from('<HBHH', data, 64)

        charts = []
        for d in range(count):
            ticks = []
            for t in range(page_size):
                index = d * page_size + t
                minutes, price, avg, vol, tick_reserved  = struct.unpack_from('<HffHH', data, 71 + index * 14)

                ticks.append({
                    'minutes': time(minutes // 60, minutes % 60),
                    'price': price,
                    'avg': avg,
                    'vol': vol,
                    'tick_reserved': tick_reserved,
                })
            charts.append({
                'date': date(pre_closes[d] // 10000, (pre_closes[d] % 10000) // 100, pre_closes[d] % 100),
                'pre_close': pre_closes[d + 5],
                'ticks': ticks
            })

        tail_offset = 71 + count * page_size * 14
        name, decimal, category, vol_unit, date_raw, time_raw, pre_close, open, high, low, close, momentum, vol, amount, tail_pad2, turnover, avg, industry = struct.unpack_from("<44sBHf5x2I5ffIf12s2fI", data, tail_offset)

        return {
            'market': MARKET(market) if not self.is_ex else EX_MARKET(market),
            'code': code.decode('gbk').rstrip('\x00'),
            'name': name.decode('gbk').rstrip('\x00'),
            'decimal': decimal,
            'category': category,
            'vol_unit': vol_unit,
            'time': datetime(date_raw // 10000, (date_raw % 10000) // 100, date_raw % 100, time_raw // 10000, (time_raw % 10000) // 100, time_raw % 100),
            'pre_close': pre_close,
            'open': open,
            'high': high,
            'low': low,
            'close': close,
            'momentum': momentum,
            'vol': vol,
            'amount': amount,
            'turnover': turnover,
            'avg': avg,
            'industry': industry,
            'charts': charts,
            'send_last': send_last,
            'tail_pad': tail_pad2.hex(),
        }
