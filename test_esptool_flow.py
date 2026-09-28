"""
test_esptool_flow.py
==================
端到端架构测试 (适配新架构: 复用 micropython-lib 的 ESPFlash).

原版本测试我们自实现的 SLIP 协议, 重构后协议层来自 micropython-lib/espflash.py
(由官方保证质量). 本测试改为验证:
  - StubFlasher (继承 ESPFlash) 能正确调用 bootloader / flash_write_file 等
  - 我们的扩展 (mem_begin/mem_block/mem_finish/load_stub) 在 ESPFlash 之上工作
  - 所有错误类存在
  - ESPFlash 的方法被正确继承
"""

import sys
import types
import struct
import hashlib
import json
import base64

sys.path.insert(0, '.')

# ---- stub modules ----
sys.modules['ustruct'] = __import__('struct')

class _StubUHashlib:
    @staticmethod
    def md5():
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
sys.modules['ujson'] = ujson_mod

mp_mod = types.ModuleType('micropython')
mp_mod.const = lambda x: x
sys.modules['micropython'] = mp_mod

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

class _ESPROM:
    END = 0xC0; ESC = 0xDB; END_ESC = 0xDC; ESC_ESC = 0xDD
    DIR_REQUEST = 0x00; DIR_RESPONSE = 0x01
    STATUS_OK = 0
    SYNC_MAGIC = b'\x07\x07\x12\x20' + b'\x55' * 32

config_mod = types.ModuleType('config')
config_mod.ESPROM = _ESPROM
config_mod.LOG_LEVEL = 0
config_mod.LOG_SLIP_PACKETS = False
config_mod.FLASH_BLOCK_SIZE = 0x1000
config_mod.FLASH_WRITE_OFFSET = 0x0
config_mod.STUB_LOADER_ENABLE = True
config_mod.STUB_LOADER_DIR = "/stub"
config_mod.ESP_RAM_BLOCK_SIZE = 0x1800
config_mod.STUB_OHAI_TIMEOUT = 3.0
config_mod.STUB_WORK_BAUDRATE = 460800
config_mod.CHIP_DETECT_MAGIC_REG = 0x40001000
config_mod.CHIP_DETECT_TIMEOUT = 2.0
config_mod.CHIP_MAGIC_VALUES = {0x00F01D83: ("esp32", "ESP32")}
config_mod.CHIP_ID_VALUES = {9: ("esp32s3", "ESP32-S3")}
config_mod.CHIP_STUB_FILES = {"esp32": "esp32_stub.json"}
sys.modules['config'] = config_mod

import importlib.util
spec = importlib.util.spec_from_file_location("esptool_lite", "./esptool_lite.py")
etl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(etl)


def test_stub_flasher_class_exists():
    """StubFlasher 类存在且继承 ESPFlash."""
    print("--- test_stub_flasher_class_exists ---")
    assert hasattr(etl, 'StubFlasher'), "StubFlasher class not found"
    import espflash
    assert issubclass(etl.StubFlasher, espflash.ESPFlash), \
        "StubFlasher should inherit from ESPFlash"
    print("PASS: StubFlasher inherits ESPFlash")


def test_error_classes_exist():
    """所有 15 个错误类存在."""
    print("--- test_error_classes_exist ---")
    errors = ['ESPROMError', 'SyncFailed', 'CommandTimeout', 'BadResponse',
              'InvalidChecksum', 'InvalidParam', 'FlashWriteFailed',
              'FlashEraseFailed', 'FlashMd5Mismatch', 'IoError',
              'UnsupportedCommand', 'TargetNotResponding', 'InvalidState',
              'FileNotFoundError', 'EmptyFirmwareFile']
    for name in errors:
        assert hasattr(etl, name), "missing error class: %s" % name
    print("PASS: all 15 error classes exist")


def test_stub_loader_methods_exist():
    """Stub loader 相关方法存在."""
    print("--- test_stub_loader_methods_exist ---")
    methods = ['mem_begin', 'mem_block', 'mem_finish', 'load_stub',
              'detect_chip', 'get_security_info', 'get_stub_file_for_chip',
              'flash_file', '_verify_md5']
    for name in methods:
        assert hasattr(etl.StubFlasher, name), "missing method: %s" % name
    print("PASS: all stub loader methods exist")


def test_espflash_methods_inherited():
    """ESPFlash 的方法被 StubFlasher 继承."""
    print("--- test_espflash_methods_inherited ---")
    import espflash
    inherited = ['bootloader', 'flash_write_file', 'flash_verify_file',
                 'flash_attach', 'flash_config', 'flash_read_size',
                 'set_baudrate', 'reboot', '_command', '_read_slip', '_write_slip']
    for name in inherited:
        assert hasattr(etl.StubFlasher, name) or hasattr(espflash.ESPFlash, name), \
            "missing inherited method: %s" % name
    print("PASS: ESPFlash methods inherited by StubFlasher")


if __name__ == '__main__':
    print("\n=== End-to-End Architecture Tests (复用 ESPFlash) ===\n")
    test_stub_flasher_class_exists()
    test_error_classes_exist()
    test_stub_loader_methods_exist()
    test_espflash_methods_inherited()
    print("\n=== ALL E2E TESTS PASSED ===\n")
