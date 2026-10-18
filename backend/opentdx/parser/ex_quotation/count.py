import struct
from opentdx.compat import override

from opentdx.parser.baseParser import BaseParser, register_parser


@register_parser(0x23f0, 1)
class Count(BaseParser): # ?
    @override
    def deserialize(self, data):
        market_id, _, _, count, _, _ = struct.unpack('<11s5I', data[:31])
        return count
