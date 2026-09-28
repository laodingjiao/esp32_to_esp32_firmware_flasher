"""
test_chip_detection.py
=====================
测试芯片型号自动识别逻辑.

借鉴 esptool.py 的 detect_chip() 流程, 测试我们 MicroPython 版本的:
  - GET_SECURITY_INFO 命令解析 (新芯片走 chip_id 路径)
  - READ_REG magic value 比对 (老芯片走 magic 路径)
  - detect_chip() 完整流程 (新芯片优先, 失败回退到老芯片路径)
  - get_stub_file_for_chip() 文件路径选择

测试用 MockUART 模拟不同芯片的响应, 不依赖真实硬件.

运行: python3 test_chip_detection.py
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
ubinascii_mod.a2b_base64 = lambda s: base64.b64decode(s)
ubinascii_mod.b2a_base64 = lambda b: base64.b64encode(b) + b'\n'
sys.modules['ubinascii'] = ubinascii_mod

ujson_mod = types.ModuleType('ujson')
ujson_mod.load = lambda f: json.load(f)
ujson_mod.loads = json.loads
ujson_mod.dump = lambda obj, f: json.dump(obj, f)
ujson_mod.dumps = json.dumps
sys.modules['ujson'] = ujson_mod

import time
utime_mod = types.ModuleType('utime')
utime_mod.ticks_ms = lambda: int(time.time() * 1000)
utime_mod.ticks_add = lambda t, d: t + d
utime_mod.ticks_diff = lambda a, b: a - b
utime_mod.sleep_ms = lambda ms: time.sleep(ms / 1000.0)
utime_mod.sleep = lambda s: time.sleep(s)
sys.modules['utime'] = utime_mod

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
    CMD_READ_REG = 0x0A
    CMD_CHANGE_BAUDRATE = 0x0F
    CMD_ERASE_FLASH = 0xD0
    CMD_FLASH_MD5 = 0x13
    CMD_GET_SECURITY_INFO = 0x14    # 新增
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
config_mod.STUB_LOADER_ENABLE = True
config_mod.STUB_LOADER_DIR = "/stub"
config_mod.ESP_RAM_BLOCK_SIZE = 0x1800
config_mod.STUB_OHAI_TIMEOUT = 3.0
config_mod.STUB_WORK_BAUDRATE = 460800
# 芯片检测配置
config_mod.CHIP_DETECT_MAGIC_REG = 0x40001000
config_mod.CHIP_DETECT_TIMEOUT = 2.0
config_mod.CHIP_MAGIC_VALUES = {
    0x00F01D83: ("esp32",   "ESP32"),
    0xFFF0C101: ("esp8266", "ESP8266"),
    0x000007C6: ("esp32s2", "ESP32-S2"),
}
config_mod.CHIP_ID_VALUES = {
    9:  ("esp32s3", "ESP32-S3"),
    12: ("esp32c2", "ESP32-C2"),
    5:  ("esp32c3", "ESP32-C3"),
    23: ("esp32c5", "ESP32-C5"),
    13: ("esp32c6", "ESP32-C6"),
    16: ("esp32h2", "ESP32-H2"),
    18: ("esp32p4", "ESP32-P4"),
}
config_mod.CHIP_STUB_FILES = {
    "esp32":   "esp32_stub.json",
    "esp32s2": "esp32s2_stub.json",
    "esp32s3": "esp32s3_stub.json",
    "esp32c2": "esp32c2_stub.json",
    "esp32c3": "esp32c3_stub.json",
    "esp32c5": "esp32c5_stub.json",
    "esp32c6": "esp32c6_stub.json",
    "esp32h2": "esp32h2_stub.json",
    "esp8266": "esp8266_stub.json",
}
sys.modules['config'] = config_mod

import importlib.util
spec = importlib.util.spec_from_file_location("esptool_lite", "./esptool_lite.py")
etl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(etl)


# ===========================================================
# MockUART: 模拟芯片响应
# ===========================================================

class MockUART:
    """模拟 UART, 支持配置不同的芯片响应."""

    def __init__(self, security_info_resp=None, magic_value=None):
        """
        :param security_info_resp: bytes, GET_SECURITY_INFO 的响应 (新芯片)
        :param magic_value: int, READ_REG 0x40001000 返回的 magic value (老芯片)
        """
        self.write_buffer = bytearray()
        self.read_buffer = bytearray()
        self._security_info_resp = security_info_resp
        self._magic_value = magic_value

    def write(self, data):
        self.write_buffer.extend(data)
        self._process_command()

    def read(self, n=None):
        if n is None:
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

            cmd = decoded[1]
            data = decoded[8:]

            self._handle_cmd(cmd, data)

    def _handle_cmd(self, cmd, data):
        if cmd == _ESPROM.CMD_GET_SECURITY_INFO:
            if self._security_info_resp is not None:
                self._send_response(cmd, self._security_info_resp)
            else:
                # 老芯片不支持此命令, 返回错误状态
                self._send_response(cmd, b'', status=1)
        elif cmd == _ESPROM.CMD_READ_REG:
            if self._magic_value is not None:
                addr = struct.unpack('<I', data)[0]
                if addr == config_mod.CHIP_DETECT_MAGIC_REG:
                    self._send_response(cmd, struct.pack('<I', self._magic_value))
                else:
                    self._send_response(cmd, struct.pack('<I', 0))
            else:
                self._send_response(cmd, struct.pack('<I', 0))
        else:
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


def make_security_info_resp(chip_id):
    """构造 GET_SECURITY_INFO 响应.

    格式 (借鉴 esptool SecurityInfo):
      - byte 0:    flags
      - byte 1-3:  flash_crypt_cnt (24 bit LE)
      - byte 4-11: key_purposes (8 bytes)
      - byte 12-15: chip_id (4 bytes LE, 关键字段)
    """
    resp = bytearray(16)
    resp[0] = 0   # flags
    resp[1:4] = b'\x00\x00\x00'   # flash_crypt_cnt = 0
    resp[4:12] = b'\x00' * 8       # key_purposes
    resp[12:16] = struct.pack('<I', chip_id)
    return bytes(resp)


# ===========================================================
# 测试用例
# ===========================================================

def test_detect_esp32_via_magic():
    """老 ESP32 走 magic value 路径 (GET_SECURITY_INFO 不支持)."""
    print("--- test_detect_esp32_via_magic ---")
    uart = MockUART(magic_value=0x00F01D83)  # ESP32 magic
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    chip_key, chip_name = tool.detect_chip()
    assert chip_key == "esp32", "expected 'esp32', got %r" % chip_key
    assert chip_name == "ESP32"
    print("PASS: detected ESP32 via magic value")


def test_detect_esp8266_via_magic():
    """ESP8266 也走 magic value 路径."""
    print("--- test_detect_esp8266_via_magic ---")
    uart = MockUART(magic_value=0xFFF0C101)
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    chip_key, chip_name = tool.detect_chip()
    assert chip_key == "esp8266"
    assert chip_name == "ESP8266"
    print("PASS: detected ESP8266 via magic value")


def test_detect_esp32s2_via_magic():
    """ESP32-S2 走 magic value 路径."""
    print("--- test_detect_esp32s2_via_magic ---")
    uart = MockUART(magic_value=0x000007C6)
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    chip_key, chip_name = tool.detect_chip()
    assert chip_key == "esp32s2"
    assert chip_name == "ESP32-S2"
    print("PASS: detected ESP32-S2 via magic value")


def test_detect_esp32s3_via_chip_id():
    """ESP32-S3 走 GET_SECURITY_INFO 路径 (chip_id=9)."""
    print("--- test_detect_esp32s3_via_chip_id ---")
    uart = MockUART(security_info_resp=make_security_info_resp(9))
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    chip_key, chip_name = tool.detect_chip()
    assert chip_key == "esp32s3", "expected 'esp32s3', got %r" % chip_key
    assert chip_name == "ESP32-S3"
    print("PASS: detected ESP32-S3 via chip_id")


def test_detect_esp32c3_via_chip_id():
    """ESP32-C3 走 GET_SECURITY_INFO 路径 (chip_id=5)."""
    print("--- test_detect_esp32c3_via_chip_id ---")
    uart = MockUART(security_info_resp=make_security_info_resp(5))
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    chip_key, chip_name = tool.detect_chip()
    assert chip_key == "esp32c3"
    assert chip_name == "ESP32-C3"
    print("PASS: detected ESP32-C3 via chip_id")


def test_detect_esp32c6_via_chip_id():
    """ESP32-C6 走 GET_SECURITY_INFO 路径 (chip_id=13)."""
    print("--- test_detect_esp32c6_via_chip_id ---")
    uart = MockUART(security_info_resp=make_security_info_resp(13))
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    chip_key, chip_name = tool.detect_chip()
    assert chip_key == "esp32c6"
    assert chip_name == "ESP32-C6"
    print("PASS: detected ESP32-C6 via chip_id")


def test_detect_esp32h2_via_chip_id():
    """ESP32-H2 走 GET_SECURITY_INFO 路径 (chip_id=16)."""
    print("--- test_detect_esp32h2_via_chip_id ---")
    uart = MockUART(security_info_resp=make_security_info_resp(16))
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    chip_key, chip_name = tool.detect_chip()
    assert chip_key == "esp32h2"
    assert chip_name == "ESP32-H2"
    print("PASS: detected ESP32-H2 via chip_id")


def test_detect_unknown_chip_id():
    """未知 chip_id 返回 (None, 'Unknown (chip_id=...)')."""
    print("--- test_detect_unknown_chip_id ---")
    uart = MockUART(security_info_resp=make_security_info_resp(999))
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    chip_key, chip_name = tool.detect_chip()
    assert chip_key is None, "expected None, got %r" % chip_key
    assert "Unknown" in chip_name
    assert "999" in chip_name
    print("PASS: unknown chip_id correctly returns None")


def test_detect_unknown_magic_value():
    """未知 magic value 返回 (None, 'Unknown (magic=...)')."""
    print("--- test_detect_unknown_magic_value ---")
    uart = MockUART(magic_value=0xDEADBEEF)
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    chip_key, chip_name = tool.detect_chip()
    assert chip_key is None
    assert "Unknown" in chip_name
    assert "DEADBEEF" in chip_name.upper()
    print("PASS: unknown magic value correctly returns None")


def test_get_stub_file_for_esp32():
    """根据 'esp32' 返回 /stub/esp32_stub.json."""
    print("--- test_get_stub_file_for_esp32 ---")
    uart = MockUART()
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    path = tool.get_stub_file_for_chip("esp32")
    assert path == "/stub/esp32_stub.json", "got %r" % path
    print("PASS: esp32 stub file path correct")


def test_get_stub_file_for_esp32s3():
    """根据 'esp32s3' 返回 /stub/esp32s3_stub.json."""
    print("--- test_get_stub_file_for_esp32s3 ---")
    uart = MockUART()
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    path = tool.get_stub_file_for_chip("esp32s3")
    assert path == "/stub/esp32s3_stub.json", "got %r" % path
    print("PASS: esp32s3 stub file path correct")


def test_get_stub_file_for_esp32p4_raises():
    """ESP32-P4 没有 stub binary, 抛 FileNotFoundError."""
    print("--- test_get_stub_file_for_esp32p4_raises ---")
    uart = MockUART()
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    try:
        tool.get_stub_file_for_chip("esp32p4")
        assert False, "should have raised FileNotFoundError"
    except etl.FileNotFoundError as e:
        assert "no stub binary" in str(e).lower() or "esp32p4" in str(e).lower()
        print("PASS: ESP32-P4 correctly raises FileNotFoundError")


def test_get_stub_file_for_none_raises():
    """chip_key=None 抛 FileNotFoundError."""
    print("--- test_get_stub_file_for_none_raises ---")
    uart = MockUART()
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    try:
        tool.get_stub_file_for_chip(None)
        assert False, "should have raised FileNotFoundError"
    except etl.FileNotFoundError as e:
        assert "not detected" in str(e).lower()
        print("PASS: chip_key=None correctly raises FileNotFoundError")


def test_security_info_parsing():
    """get_security_info 返回的 dict 字段正确."""
    print("--- test_security_info_parsing ---")
    resp = make_security_info_resp(9)   # ESP32-S3
    uart = MockUART(security_info_resp=resp)
    tool = etl.StubFlasher(lambda v: None, lambda v: None, uart)
    info = tool.get_security_info()
    assert info['chip_id'] == 9, "expected chip_id=9, got %r" % info['chip_id']
    assert info['flags'] == 0
    assert info['flash_crypt_cnt'] == 0
    assert len(info['key_purposes']) == 8
    print("PASS: get_security_info dict correct")


if __name__ == '__main__':
    print("\n=== Chip Detection Tests ===\n")
    test_detect_esp32_via_magic()
    test_detect_esp8266_via_magic()
    test_detect_esp32s2_via_magic()
    test_detect_esp32s3_via_chip_id()
    test_detect_esp32c3_via_chip_id()
    test_detect_esp32c6_via_chip_id()
    test_detect_esp32h2_via_chip_id()
    test_detect_unknown_chip_id()
    test_detect_unknown_magic_value()
    test_get_stub_file_for_esp32()
    test_get_stub_file_for_esp32s3()
    test_get_stub_file_for_esp32p4_raises()
    test_get_stub_file_for_none_raises()
    test_security_info_parsing()
    print("\n=== ALL CHIP DETECTION TESTS PASSED ===\n")
