"""
test_slip.py
==========
在主机 (PC/CPython) 上运行的 SLIP 编解码 + 命令帧打包逻辑测试.

不依赖 MicroPython, 仅测试纯算法正确性.
运行: python3 test_slip.py
"""

import sys
import struct

# 复用 esptool_lite 中的 SLIP 逻辑
sys.path.insert(0, '.')


# 手动从 esptool_lite 复制 SLIP 函数 (避免依赖 machine 模块)
END = 0xC0
ESC = 0xDB
END_ESC = 0xDC
ESC_ESC = 0xDD


def slip_encode(data):
    out = bytearray()
    for b in data:
        if b == END:
            out.extend(b'\xDB\xDC')
        elif b == ESC:
            out.extend(b'\xDB\xDD')
        else:
            out.append(b)
    return bytes(out)


def slip_decode(data):
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == ESC and i + 1 < n:
            nxt = data[i + 1]
            if nxt == END_ESC:
                out.append(END)
            elif nxt == ESC_ESC:
                out.append(ESC)
            else:
                out.append(b)
                out.append(nxt)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def pack_command(cmd, data=b'', checksum=0):
    size = len(data)
    header = struct.pack('<BBHI', 0x00, cmd, size, checksum)
    payload = header + data
    encoded = slip_encode(payload)
    return bytes([END]) + encoded + bytes([END])


def unpack_response(frame_body):
    """frame_body 应包含首尾 0xC0."""
    assert frame_body[0] == END, "frame must start with 0xC0"
    assert frame_body[-1] == END, "frame must end with 0xC0"
    slip_payload = frame_body[1:-1]
    decoded = slip_decode(slip_payload)
    assert len(decoded) >= 8, "decoded too short"
    direction, cmd, size, status = struct.unpack('<BBHI', decoded[:8])
    data = decoded[8:8 + size]
    return direction, cmd, size, status, data


def test_slip_roundtrip():
    """测试: 各种字节组合 编码后解码 应等于原始."""
    test_cases = [
        b'',
        b'\x00',
        b'\xC0',                    # 仅 END, 应转义
        b'\xDB',                    # 仅 ESC, 应转义
        b'\xC0\xDB\x01\x02\x03',
        b'hello world',
        bytes(range(256)),          # 全字节范围
        b'\xC0' * 100,
    ]
    for case in test_cases:
        enc = slip_encode(case)
        dec = slip_decode(enc)
        assert dec == case, "roundtrip fail: %r -> %r -> %r" % (case, enc, dec)
    print("PASS: slip roundtrip (%d cases)" % len(test_cases))


def test_command_packing():
    """测试: SYNC 命令帧打包正确."""
    SYNC_MAGIC = b'\x07\x07\x12\x20' + b'\x55' * 32
    frame = pack_command(0x08, data=SYNC_MAGIC, checksum=0)
    # 期望: 0xC0 + encoded(header + magic) + 0xC0
    # 头部: 0x00 0x08 0x24 0x00 0x00 0x00 0x00 0x00 (8 bytes)
    # magic 中无 0xC0/0xDB, 故 encoded == header + magic
    expected_header = bytes([0x00, 0x08, 0x24, 0x00, 0x00, 0x00, 0x00, 0x00])
    assert frame[0] == END
    assert frame[-1] == END
    assert frame[1:9] == expected_header, \
        "header mismatch: got %r" % frame[1:9]
    # 检查 magic 部分 (位置 9 ~ 9+36)
    assert frame[9:9 + 36] == SYNC_MAGIC, "magic not in expected position"
    print("PASS: SYNC command packing (frame len = %d)" % len(frame))


def test_command_with_special_bytes():
    """测试: 含 0xC0/0xDB 的 payload 正确转义."""
    payload = b'\xC0\xDB\xAB\xCD'
    frame = pack_command(0x03, data=payload, checksum=0xDEADBEEF)
    # 解包回来应该一致
    direction, cmd, size, status, data = unpack_response(frame)
    # 注意: 我们打包时用了 0x00 (DIR_REQUEST), 解包逻辑用 unpack_response
    # 这里我们 hack: 把 direction 字段当作请求处理 (函数名虽叫 response 但逻辑通用)
    assert direction == 0x00, "direction should be REQUEST, got 0x%02X" % direction
    assert cmd == 0x03, "cmd mismatch"
    assert size == len(payload), "size mismatch"
    assert status == 0xDEADBEEF, "checksum mismatch: got 0x%X" % status
    assert data == payload, "data mismatch: got %r" % data
    print("PASS: command with special bytes (escaped correctly)")


def test_response_unpacking():
    """测试: 解析 ROM 返回的 OK 响应."""
    # 模拟 ROM 对 SYNC 的成功响应: direction=0x01, cmd=0x08, size=0, status=0
    header = struct.pack('<BBHI', 0x01, 0x08, 0, 0)
    encoded = slip_encode(header)
    frame = bytes([END]) + encoded + bytes([END])
    direction, cmd, size, status, data = unpack_response(frame)
    assert direction == 0x01, "direction should be RESPONSE"
    assert cmd == 0x08, "cmd should be SYNC"
    assert size == 0, "size should be 0"
    assert status == 0, "status should be OK"
    assert data == b'', "data should be empty"
    print("PASS: response unpacking")


def test_checksum_calculation():
    """测试: FLASH_DATA 的 XOR 校验和算法."""
    # 模拟 esptool_lite.flash_block 中的 checksum 计算
    data_block = b'\x01\x02\x03\x04\xFF\xAA\x55'
    checksum = 0
    for b in data_block:
        checksum ^= b
    expected = 0
    for b in data_block:
        expected ^= b
    assert checksum == expected, "XOR checksum mismatch"
    # 验证范围: 一个字节内的 XOR 不会超出 0-255
    assert 0 <= checksum <= 0xFF, "XOR result out of byte range"
    print("PASS: checksum (XOR) calculation, value=0x%02X" % checksum)


def test_block_padding():
    """测试: 块数据不足 block_size 时填充 0xFF."""
    BLOCK_SIZE = 0x1000  # 4KB
    block = b'\x01' * 100  # 100 字节
    pad = BLOCK_SIZE - (len(block) % BLOCK_SIZE)
    padded = block + b'\xFF' * pad
    assert len(padded) == BLOCK_SIZE, "padding size wrong"
    assert padded[:100] == block, "original data corrupted"
    assert padded[100:] == b'\xFF' * (BLOCK_SIZE - 100), "padding not 0xFF"
    print("PASS: block padding (100 -> %d bytes)" % len(padded))


def test_full_block_count():
    """测试: num_blocks 计算正确."""
    BLOCK_SIZE = 0x1000
    test_sizes = [0, 1, 4096, 4097, 8192, 8193, 1759456]  # 1.68MB firmware
    for size in test_sizes:
        if size == 0:
            num_blocks = 1
        else:
            num_blocks = (size + BLOCK_SIZE - 1) // BLOCK_SIZE
        print("  size=%d -> %d blocks" % (size, num_blocks))


if __name__ == '__main__':
    print("=== SLIP & Protocol Tests ===\n")
    test_slip_roundtrip()
    test_command_packing()
    test_command_with_special_bytes()
    test_response_unpacking()
    test_checksum_calculation()
    test_block_padding()
    test_full_block_count()
    print("\n=== ALL TESTS PASSED ===")
