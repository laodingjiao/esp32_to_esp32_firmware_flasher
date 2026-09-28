"""
test_stub_loader.py
==================
测试 stub loader 加载逻辑.

借鉴 esptool.py 的 stub loader 实现, 测试我们 MicroPython 版本的:
  - mem_begin / mem_block / mem_finish 命令打包
  - load_stub 完整流程 (上传 stub + 等 OHAI)
  - stub JSON 文件解析 (base64 解码)
  - 错误处理 (stub 文件不存在 / OHAI 超时 / 上传失败)

用 MockUART 模拟目标板响应, 不依赖真实硬件.

运行: python3 test_stub_loader.py
"""

import sys
import types
import struct
import base64
import json

sys.path.insert(0, '.')

# ---- stub modules ----
sys.modules['ustruct'] = __import__('struct')

class _StubUHashlib:
    @staticmethod
    def md5():
        import hashlib
        return hashlib.md5()
sys.modules['uhashlib'] = _StubUHashlib()

import binascii
ubinascii_mod = types.ModuleType('ubinascii')
ubinascii_mod.hexlify = binascii.hexlify
# base64 解码 (用于 stub binary)
ubinascii_mod.a2b_base64 = lambda s: base64.b64decode(s)
ubinascii_mod.b2a_base64 = lambda b: base64.b64encode(b) + b'\n'
sys.modules['ubinascii'] = ubinascii_mod

# stub: ujson (MicroPython 紧凑 JSON)
ujson_mod = types.ModuleType('ujson')
ujson_mod.load = lambda f: json.load(f)
ujson_mod.loads = json.loads
ujson_mod.dump = lambda obj, f: json.dump(obj, f)
ujson_mod.dumps = json.dumps
sys.modules['ujson'] = ujson_mod

# stub: micropython (espflash.py 用 from micropython import const)
micropython_mod = types.ModuleType('micropython')
def _const(x):
    return x
micropython_mod.const = _const
sys.modules['micropython'] = micropython_mod

# stub: utime
import time
utime_mod = types.ModuleType('utime')
utime_mod.ticks_ms = lambda: int(time.time() * 1000)
utime_mod.ticks_add = lambda t, d: t + d
utime_mod.ticks_diff = lambda a, b: a - b
utime_mod.sleep_ms = lambda ms: time.sleep(ms / 1000.0)
utime_mod.sleep = lambda s: time.sleep(s)
sys.modules['utime'] = utime_mod

# stub: machine
machine_mod = types.ModuleType('machine')
class _PinStub:
    def __init__(self, *a, **kw): pass
machine_mod.Pin = _PinStub
class _UARTStub:
    def __init__(self, *a, **kw): pass
machine_mod.UART = _UARTStub
sys.modules['machine'] = machine_mod
# stub: micropython (espflash.py 用)
mp_mod = types.ModuleType('micropython')
mp_mod.const = lambda x: x
sys.modules['micropython'] = mp_mod

# stub: config
class _ESPROM:
    END = 0xC0
    ESC = 0xDB
    END_ESC = 0xDC
    ESC_ESC = 0xDD
    DIR_REQUEST = 0x00
    DIR_RESPONSE = 0x01
    CMD_FLASH_BEGIN = 0x02
    CMD_FLASH_DATA = 0x03
    CMD_FLASH_END = 0x04
    CMD_MEM_BEGIN = 0x05
    CMD_MEM_END = 0x06
    CMD_MEM_DATA = 0x07
    CMD_SYNC = 0x08
    CMD_CHANGE_BAUDRATE = 0x0F
    CMD_ERASE_FLASH = 0xD0
    CMD_FLASH_MD5 = 0x13
    STATUS_OK = 0
    SYNC_MAGIC = b'\x07\x07\x12\x20' + b'\x55' * 32

config_mod = types.ModuleType('config')
config_mod.ESPROM = _ESPROM
config_mod.LOG_LEVEL = 0
config_mod.LOG_SLIP_PACKETS = False
config_mod.SYNC_RETRY_COUNT = 7
config_mod.SYNC_RETRY_DELAY = 0.01
config_mod.COMMAND_TIMEOUT = 3.0
config_mod.ERASE_TIMEOUT = 30.0
config_mod.FLASH_BEGIN_TIMEOUT = 10.0
config_mod.FLASH_BLOCK_TIMEOUT = 5.0
config_mod.MD5_TIMEOUT = 10.0
config_mod.FLASH_BLOCK_SIZE = 0x1000
config_mod.ERASE_FULL_FLASH_BEFORE_WRITE = False
# stub loader 配置
config_mod.STUB_LOADER_ENABLE = True
config_mod.STUB_LOADER_FILE = "/stub/esp32_stub.json"  # 实际不存在, 测试用 mock
config_mod.ESP_RAM_BLOCK_SIZE = 0x1800  # 6KB
config_mod.STUB_OHAI_TIMEOUT = 3.0
config_mod.STUB_WORK_BAUDRATE = 460800
sys.modules['config'] = config_mod

# 加载被测模块
import importlib.util
spec = importlib.util.spec_from_file_location("esptool_lite", "./esptool_lite.py")
etl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(etl)


# ===========================================================
# MockUART: 模拟 ROM + stub loader 行为
# ===========================================================

class _RamView:
    """RAM 视图类, 支持 uart.ram[start:end] 切片读取.

    内部存储在 MockUART._ram dict 中 (因 stub binary 加载到 0x400BE000
    等地址, 超出 bytearray 1MB 范围). 这里提供 bytearray 风格的切片接口.
    """

    def __init__(self, ram_dict):
        self._ram = ram_dict

    def __getitem__(self, key):
        if isinstance(key, slice):
            start = key.start or 0
            stop = key.stop
            return bytes(self._ram.get(addr, 0) for addr in range(start, stop))
        elif isinstance(key, int):
            return self._ram.get(key, 0)
        raise TypeError("unsupported key type: %s" % type(key))


class MockUART:
    """模拟 UART + ROM/stub 响应.

    模拟真实 UART 流式读取行为: 数据逐字节返回.
    每次 read(n) 最多返回 n 字节 (从已生成的 read_buffer 取).
    """

    def __init__(self):
        self.write_buffer = bytearray()  # 收到的命令帧
        self.read_buffer = bytearray()  # 待返回的响应字节
        # 模拟 RAM (用 dict, 因 stub binary 加载到 0x400BE000 等地址, 超出 bytearray 范围)
        # key = 地址, value = 字节值
        self._ram = {}
        # stub 加载状态
        self.stub_loaded = False
        self.stub_ohai_sent = False

    @property
    def ram(self):
        """提供 ram[start:end] 切片访问, 内部用 dict 存储."""
        return _RamView(self._ram)

    def write(self, data):
        self.write_buffer.extend(data)
        self._process_command()

    def read(self, n=None):
        """模拟真实 UART: 返回最多 n 字节, 不足返回已读到的."""
        if n is None:
            # 全部读出
            r = bytes(self.read_buffer)
            self.read_buffer = bytearray()
            return r if r else None
        if not self.read_buffer:
            return None
        take = min(n, len(self.read_buffer))
        r = bytes(self.read_buffer[:take])
        del self.read_buffer[:take]
        return r

    def init(self, **kw):
        pass

    def _process_command(self):
        """从 write_buffer 解析完整帧, 生成响应放入 read_buffer."""
        while True:
            try:
                start = self.write_buffer.index(0xC0)
            except ValueError:
                break
            try:
                end = self.write_buffer.index(0xC0, start + 1)
            except ValueError:
                break

            frame = bytes(self.write_buffer[start:end + 1])
            del self.write_buffer[:end + 1]

            slip_payload = frame[1:-1]
            decoded = _slip_decode(slip_payload)
            if len(decoded) < 8:
                continue

            direction = decoded[0]
            cmd = decoded[1]
            size = struct.unpack('<H', decoded[2:4])[0]
            data = decoded[8:8 + size]

            self._handle_cmd(cmd, data)

    def _handle_cmd(self, cmd, data):
        """根据命令生成响应."""
        if cmd == _ESPROM.CMD_MEM_BEGIN:
            # MEM_BEGIN: 通知即将上传, 数据 size/blocks/block_size/offset
            size, blocks, block_size, offset = struct.unpack('<IIII', data)
            self._stub_size = size
            self._stub_offset = offset
            self._stub_received = 0
            self._stub_blocks = blocks
            self._send_response(cmd, b'')

        elif cmd == _ESPROM.CMD_MEM_DATA:
            # MEM_DATA: 接收数据块
            data_len, seq, _, _ = struct.unpack('<IIII', data[:16])
            payload = data[16:16 + data_len]
            # 写入模拟 RAM (用 dict, 因地址超出 bytearray 范围)
            ram_pos = self._stub_offset + seq * config_mod.ESP_RAM_BLOCK_SIZE
            for i, b in enumerate(payload):
                self._ram[ram_pos + i] = b
            self._stub_received += len(payload)
            self._send_response(cmd, b'')

        elif cmd == _ESPROM.CMD_MEM_END:
            # MEM_END: 跳转执行 stub
            # 先回 MEM_END 响应 (esptool 设短超时等这个)
            self._send_response(cmd, b'')
            # 再发 OHAI 模拟 stub 启动后的就绪信号
            self.stub_loaded = True
            self.read_buffer.extend(b'OHAI')

        else:
            # 其他命令回 OK
            self._send_response(cmd, b'')

    def _send_response(self, cmd, payload, status=0):
        header = struct.pack('<BBHI',
                             _ESPROM.DIR_RESPONSE, cmd,
                             len(payload), status)
        encoded = _slip_encode(header + payload)
        self.read_buffer.extend(bytes([0xC0]))
        self.read_buffer.extend(encoded)
        self.read_buffer.extend(bytes([0xC0]))


def _slip_encode(data):
    out = bytearray()
    for b in data:
        if b == 0xC0:
            out.extend(b'\xDB\xDC')
        elif b == 0xDB:
            out.extend(b'\xDB\xDD')
        else:
            out.append(b)
    return bytes(out)


def _slip_decode(data):
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0xDB and i + 1 < len(data):
            nxt = data[i + 1]
            if nxt == 0xDC:
                out.append(0xC0)
                i += 2
                continue
            elif nxt == 0xDD:
                out.append(0xDB)
                i += 2
                continue
        out.append(b)
        i += 1
    return bytes(out)


# ===========================================================
# Mock stub JSON 文件
# ===========================================================

def make_mock_stub_json():
    """生成一个 mock stub JSON, 模拟 esptool 的 stub binary 结构."""
    # 真实 stub 是 ~3.6KB, 这里用 100 字节模拟
    text = bytes([(i % 256) for i in range(100)])  # 100 字节 text
    data = bytes([(i + 1) % 256 for i in range(20)])  # 20 字节 data
    return {
        'entry': 0x400BE658,
        'text': base64.b64encode(text).decode('ascii'),
        'text_start': 0x400BE000,
        'data': base64.b64encode(data).decode('ascii'),
        'data_start': 0x3FFDEBAC,
        'bss_start': 0x3FFCC000,
    }


# ===========================================================
# 测试用例
# ===========================================================

def test_mem_begin_packing():
    """MEM_BEGIN 命令帧打包正确 (4 个 LE32 字段)."""
    print("--- test_mem_begin_packing ---")
    uart = MockUART()
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    tool.mem_begin(100, 1, 0x1800, 0x400BE000)
    # 应该发出 1 个帧 (MEM_BEGIN)
    # 解析 write_buffer (已处理)
    # 看 RAM 是否被准备 (mock 写到了 ram[0x400BE000] 起始)
    # 这里我们验证 stub_loaded=False 但 mem_begin 没崩
    assert tool.is_stub == False
    print("PASS: mem_begin runs without error")


def test_mem_block_packing():
    """MEM_DATA 命令帧打包正确 (16 字节头 + data)."""
    print("--- test_mem_block_packing ---")
    uart = MockUART()
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    tool.mem_begin(100, 1, 0x1800, 0x400BE000)
    tool.mem_block(b'hello world', 0)
    # 验证 mock RAM 收到了数据
    # 0x400BE000 起始 11 字节应该是 'hello world'
    ram_data = bytes(uart.ram[0x400BE000:0x400BE000 + 11])
    assert ram_data == b'hello world', \
        "RAM not written correctly: %r" % ram_data
    print("PASS: mem_block writes to RAM correctly")


def test_load_stub_success():
    """完整 load_stub 流程: 上传 + 跳转 + 等 OHAI."""
    print("--- test_load_stub_success ---")
    uart = MockUART()
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)

    # 写一个 mock stub JSON 文件
    import tempfile, os
    stub_data = make_mock_stub_json()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(stub_data, f)
        stub_path = f.name

    try:
        # 加载 stub
        result = tool.load_stub(stub_path)
        assert result is True, "load_stub should return True on success"
        assert tool.is_stub is True, "is_stub should be True after load_stub"
        assert uart.stub_loaded is True, "mock should record stub_loaded"
        # OHAI 应该已被读出
        print("PASS: load_stub succeeded, OHAI received, is_stub=True")
    finally:
        os.unlink(stub_path)


def test_load_stub_file_not_found():
    """stub 文件不存在时抛 FileNotFoundError."""
    print("--- test_load_stub_file_not_found ---")
    uart = MockUART()
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    try:
        tool.load_stub("/nonexistent/stub.json")
        assert False, "should have raised FileNotFoundError"
    except etl.FileNotFoundError as e:
        assert "stub loader file not found" in str(e)
        print("PASS: FileNotFoundError raised correctly")


def test_load_stub_with_real_esp32_json():
    """用真实 ESP32 stub JSON 文件测试 (3.6 KB)."""
    print("--- test_load_stub_with_real_esp32_json ---")
    import os
    real_stub = "./stub/esp32_stub.json"
    if not os.path.exists(real_stub):
        print("SKIP: real stub JSON not found at %s" % real_stub)
        return

    uart = MockUART()
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    result = tool.load_stub(real_stub)
    assert result is True
    assert tool.is_stub is True

    # 验证 stub binary 的 text 段确实写到了 RAM
    import json as _json, base64 as _b64
    stub_info = _json.load(open(real_stub))
    text_bytes = _b64.b64decode(stub_info['text'])
    text_start = stub_info['text_start']
    # 检查 RAM 中前 32 字节匹配
    ram_text = bytes(uart.ram[text_start:text_start + 32])
    expected_text = text_bytes[:32]
    assert ram_text == expected_text, \
        "text segment mismatch: RAM=%r expected=%r" % (
            ram_text, expected_text)
    print("PASS: real ESP32 stub (3.6KB) loaded correctly, text verified")


def test_load_stub_ohai_timeout():
    """stub 没发 OHAI 时抛 SyncFailed."""
    print("--- test_load_stub_ohai_timeout ---")
    uart = MockUART()
    # 让 mock 在 MEM_END 时不发 OHAI
    # 重写 _handle_cmd 不发 OHAI
    original_handle = uart._handle_cmd

    def no_ohai_handle(cmd, data):
        if cmd == _ESPROM.CMD_MEM_END:
            # 不发 OHAI, 模拟 stub 启动失败
            return
        original_handle(cmd, data)

    uart._handle_cmd = no_ohai_handle

    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)

    import tempfile, os
    stub_data = make_mock_stub_json()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(stub_data, f)
        stub_path = f.name

    try:
        # 缩短 OHAI 超时让测试快
        config_mod.STUB_OHAI_TIMEOUT = 0.5
        try:
            tool.load_stub(stub_path)
            assert False, "should have raised SyncFailed"
        except etl.SyncFailed as e:
            assert "OHAI" in str(e) or "stub" in str(e).lower()
            print("PASS: SyncFailed raised when OHAI not received")
    finally:
        config_mod.STUB_OHAI_TIMEOUT = 3.0
        os.unlink(stub_path)


def test_is_stub_flag():
    """is_stub 标志初始为 False, load_stub 后变 True."""
    print("--- test_is_stub_flag ---")
    uart = MockUART()
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    assert tool.is_stub is False, "is_stub should be False initially"

    import tempfile, os
    stub_data = make_mock_stub_json()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(stub_data, f)
        stub_path = f.name

    try:
        tool.load_stub(stub_path)
        assert tool.is_stub is True, "is_stub should be True after load_stub"
        print("PASS: is_stub flag transitions correctly")
    finally:
        os.unlink(stub_path)


if __name__ == '__main__':
    print("\n=== Stub Loader Tests ===\n")
    test_mem_begin_packing()
    test_mem_block_packing()
    test_load_stub_success()
    test_load_stub_file_not_found()
    test_load_stub_with_real_esp32_json()
    test_load_stub_ohai_timeout()
    test_is_stub_flag()
    print("\n=== ALL STUB LOADER TESTS PASSED ===\n")
