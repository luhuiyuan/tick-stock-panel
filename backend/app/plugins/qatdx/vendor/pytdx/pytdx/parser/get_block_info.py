# coding=utf-8

from pytdx.parser.base import BaseParser
from pytdx.reader.block_reader import BlockReader,BlockReader_TYPE_FLAT
from pytdx.helper import get_datetime, get_volume, get_price
from collections import OrderedDict
import struct
import six



class GetBlockInfoMeta(BaseParser):
    def setParams(self, block_file):
        if type(block_file) is six.text_type:
            block_file = block_file.encode("utf-8")
        pkg = bytearray.fromhex(u'0C 39 18 69 00 01 2A 00 2A 00 C5 02')
        pkg.extend(struct.pack(u"<{}s".format(0x2a - 2), block_file))
        self.send_pkg = pkg


    def parseResponse(self, body_buf):
        (size, _, hash_value, _ ) = struct.unpack(u"<I1s32s1s", body_buf)
        return {
            "size": size,
            "hash_value" : hash_value
        }

class GetBlockInfo(BaseParser):

    def setParams(self, block_file, start, size):
        if type(block_file) is six.text_type:
            block_file = block_file.encode("utf-8")
        pkg = bytearray.fromhex(u'0c 37 18 6a 00 01 6e 00 6e 00 b9 06')
        #pkg = bytearray.fromhex(u'0c 33 18 6a 00 01 6e 00 6e 00 b9 06 60 ea 00 00 30 75 00 00')
        pkg.extend(struct.pack(u"<II{}s".format(0x6e-10), start, size, block_file))
        self.send_pkg = pkg

    def parseResponse(self, body_buf):
        return body_buf[4:]



def get_and_parse_block_info(client, blockfile):
    """按实际回包长度推进, 不要用固定 chunk 步长。

    主站每页回 60000 字节(0xEA60), 而原实现按 0x7530(30000) 推进: 第 2 页请求的
    偏移落在第 1 页中间, 拼出来的文件是重叠的, 于是只有最前面几十条板块记录正确,
    之后全体错位 —— 表现为板块名读出 '\\x00600150\\x006'、成分数读出 12336 这类
    垃圾, 一个 28 万字节的 block.dat 能产出 80 万行。
    """
    try:
        meta = client.get_block_info_meta(blockfile)
    except Exception as e:
        return None

    if not meta:
        return None

    size = meta['size']
    file_content = bytearray()
    while len(file_content) < size:
        piece = client.get_block_info(blockfile, len(file_content), size) or b""
        if not piece:
            break
        file_content.extend(piece)

    return BlockReader().get_data(file_content, BlockReader_TYPE_FLAT)


if __name__ == '__main__':
    from pytdx.hq import TdxHq_API
    api = TdxHq_API()
    with api.connect():
        # ret = api.get_block_info("block_zs.dat", 0, 100)
        # print(len(ret))
        # ret = api.get_and_parse_block_info("block_fg.dat")
        # ret = api.get_and_parse_block_info("block_zs.dat")
        # ret = api.get_and_parse_block_info("block_gn.dat")
        # ret = api.get_and_parse_block_info("block.dat")
        ret = api.get_and_parse_block_info("block.dat")
        print(api.to_df(ret))


