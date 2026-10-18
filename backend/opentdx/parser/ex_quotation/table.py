import struct
from opentdx.compat import override

from opentdx.parser.baseParser import BaseParser, register_parser

@register_parser(0x2422, 1)
class Table(BaseParser):
    def __init__(self, start: int = 0, mode: int = 1):
        self.body = bytearray(struct.pack('<II16s85xB16x', start, 0, bytes.fromhex('00781f0e6a37447b502b7c0d01404c0a'), mode))

    @override
    def deserialize(self, data):
        # data[0:35]: header — 32 bytes reserved + category(H) + flag(B) + tail(B)
        reserved_header = data[:32]
        category, flag, tail_byte = struct.unpack('<HBB', data[32:35])
        start, = struct.unpack('<I', data[35:39])
        # data[39:55]: server echo (16 bytes, mirrors request id)
        server_echo = data[39:55]
        # data[55:161]: mostly zero padding with occasional flags
        flag2, = struct.unpack('<B', data[116:117])
        count, ctx_len = struct.unpack('<II', data[161:169])
        ctx = data[169:].decode('gbk', errors='ignore').replace('\x00', '')
        return start, count, ctx
