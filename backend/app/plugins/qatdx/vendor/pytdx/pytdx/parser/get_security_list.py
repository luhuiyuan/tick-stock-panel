# coding=utf-8

from pytdx.parser.base import BaseParser
from pytdx.helper import get_datetime, get_volume, get_price
from collections import OrderedDict
import struct


class GetSecurityList(BaseParser):

    def setParams(self, market, start):
        pkg = bytearray.fromhex(u'0c 01 18 64 01 01 06 00 06 00 50 04')
        pkg_param = struct.pack("<HH", market, start)
        pkg.extend(pkg_param)
        self.send_pkg = pkg

    def parseResponse(self, body_buf):

        pos = 0
        (num, ) = struct.unpack("<H", body_buf[:2])
        pos += 2
        stocks = []
        for i in range(num):

            # b'880023d\x00\xd6\xd0\xd0\xa1\xc6\xbd\xbe\xf9.9\x04\x00\x02\x9a\x99\x8cA\x00\x00\x00\x00'
            # 880023 100 中小平均 276782 2 17.575001 0 80846648

            one_bytes = body_buf[pos: pos + 29]

            (code, volunit,
             name_bytes, reversed_bytes1, decimal_point,
             pre_close_raw, reversed_bytes2) = struct.unpack("<6sH8s4sBI4s", one_bytes)

            code = code.decode("utf-8")
            # name 字段固定 8 字节(GBK 最多 4 字)。超过 4 字的中文名会被协议从
            # 中间截断, 末尾留下半个汉字 → 严格解码抛 UnicodeDecodeError, 使整页
            # (最多 1000 只) 解析失败, 进而让 get_security_list 完全取不到数据。
            # 这里容错解码: 丢掉残缺的尾字节, 保留可解码部分。
            name = name_bytes.decode("gbk", errors="ignore").rstrip("\x00")
            pre_close = get_volume(pre_close_raw)
            pos += 29

            one = OrderedDict(
                [
                    ('code', code),
                    ('volunit', volunit),
                    ('decimal_point', decimal_point),
                    ('name', name),
                    ('pre_close', pre_close),
                ]
            )

            stocks.append(one)


        return stocks