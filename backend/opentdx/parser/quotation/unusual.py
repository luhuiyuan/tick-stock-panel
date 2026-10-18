from datetime import time
import struct
from opentdx.compat import override

from opentdx.const import MARKET
from opentdx.parser.baseParser import BaseParser, register_parser
from opentdx.utils.help import unpack_by_type


@register_parser(0x563)
class Unusual(BaseParser): # 主力监控
    def __init__(self, market: MARKET, start: int, count: int = 600):
        self.body = struct.pack('<HII', market.value, start, count)

    @override
    def deserialize(self, data):
        count, = struct.unpack('<H', data[:2])
        results = []
        for i in range(count):
            offset = 32 * i
            market, code, _, unusual_type, _, index, z = struct.unpack('<H6sBBBHH', data[offset + 2: offset + 17])
            desc, value, v1, v2, v3, v4  = unpack_by_type(unusual_type, data[offset + 17: offset + 30])
            flag, = struct.unpack('<B', data[offset + 30: offset + 31])
            hour, minute_sec = struct.unpack('<BH', data[offset + 31: offset + 34])

            results.append({
                'index': index,
                'market': MARKET(market),
                'code': code.decode('gbk').replace('\x00', ''),
                'time': time(hour, minute_sec // 100, minute_sec % 100),
                'desc': desc,
                'value': value,
                'unusual_type': unusual_type,
                'v1': v1,
                'v2': v2,
                'v3': v3,
                'v4': v4,
                'flag': flag,
            })
        return results
