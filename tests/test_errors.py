"""
test_errors.py
==============
测试 esptool_lite.py 的扩展错误码体系.

借鉴 espressif/esp-serial-flasher 的 esp_loader_error_t (30+ 错误码),
我们实现了 15 个细分错误, 方便上层针对性处理.

本测试验证:
  - 所有错误类都继承自 ESPROMError (向上兼容)
  - ERR_CODE 字段正确
  - 错误信息格式正确
  - 子类继承关系正确 (FileNotFoundError 是 FlashWriteFailed 子类等)
  - except 顺序正确 (先具体后通用)

注意: 由于 esptool_lite.py 顶部 `import ustruct` (MicroPython 模块),
本测试通过手动加载方式跳过 ustruct, 仅测试错误类定义.

运行: python3 test_errors.py
"""

import sys
import importlib.util
import types
import json

import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 手动加载 esptool_lite.py, stub 掉 ustruct/uhashlib/ubinascii/machine
# (本测试只关心错误类定义, 不需要这些模块)
sys.modules['ustruct'] = __import__('struct')

class _StubUHashlib:
    @staticmethod
    def md5():
        import hashlib
        return hashlib.md5()
sys.modules['uhashlib'] = _StubUHashlib()

import binascii
ubinascii_mod = type(sys)('ubinascii')
ubinascii_mod.hexlify = binascii.hexlify
sys.modules['ubinascii'] = ubinascii_mod

# stub: ujson
ujson_mod = types.ModuleType('ujson')
ujson_mod.load = lambda f: json.load(f)
ujson_mod.loads = json.loads
sys.modules['ujson'] = ujson_mod

# stub: micropython (espflash.py 用)
mp_mod = types.ModuleType('micropython')
mp_mod.const = lambda x: x
sys.modules['micropython'] = mp_mod

# stub: utime (esptool_lite 用 utime.ticks_ms / ticks_add / ticks_diff / sleep_ms / sleep)
import time
utime_mod = type(sys)('utime')
utime_mod.ticks_ms = lambda: int(time.time() * 1000)
utime_mod.ticks_add = lambda t, d: t + d
utime_mod.ticks_diff = lambda a, b: a - b
utime_mod.sleep_ms = lambda ms: time.sleep(ms / 1000.0)
utime_mod.sleep = lambda s: time.sleep(s)
sys.modules['utime'] = utime_mod

machine_mod = type(sys)('machine')
class _PinStub:
    def __init__(self, *a, **kw): pass
machine_mod.Pin = _PinStub
class _UARTStub:
    def __init__(self, *a, **kw): pass
machine_mod.UART = _UARTStub
sys.modules['machine'] = machine_mod

# 用 importlib 加载, 跳过顶部的 micro 导入
spec = importlib.util.spec_from_file_location(
    "esptool_lite", os.path.join(os.path.dirname(__file__), "..", "src", "esptool_lite.py")
)
esptool_lite = importlib.util.module_from_spec(spec)
# 因为 stub 已注入 sys.modules, 顶部的 import ustruct 等会成功
spec.loader.exec_module(esptool_lite)


from esptool_lite import (
    ESPROMError,
    SyncFailed, CommandTimeout, BadResponse,
    InvalidChecksum, InvalidParam,
    FlashWriteFailed, FlashEraseFailed, FlashMd5Mismatch,
    IoError, UnsupportedCommand,
    TargetNotResponding, InvalidState,
    FileNotFoundError, EmptyFirmwareFile,
)


def test_inheritance():
    """所有具体错误都继承自 ESPROMError."""
    print("--- test_inheritance ---")
    errors = [
        SyncFailed, CommandTimeout, BadResponse,
        InvalidChecksum, InvalidParam,
        FlashWriteFailed, FlashEraseFailed,
        IoError, UnsupportedCommand,
        TargetNotResponding, InvalidState,
    ]
    for cls in errors:
        assert issubclass(cls, ESPROMError), \
            "%s should inherit ESPROMError" % cls.__name__
    print("PASS: all 11 base errors inherit ESPROMError")


def test_file_errors_inherit_flash_write():
    """FileNotFoundError 和 EmptyFirmwareFile 是 FlashWriteFailed 子类."""
    print("--- test_file_errors_inherit_flash_write ---")
    assert issubclass(FileNotFoundError, FlashWriteFailed)
    assert issubclass(EmptyFirmwareFile, FlashWriteFailed)
    print("PASS: FileNotFoundError and EmptyFirmwareFile inherit FlashWriteFailed")


def test_md5_mismatch_inherits_flash_write():
    """FlashMd5Mismatch 是 FlashWriteFailed 子类."""
    print("--- test_md5_mismatch_inherits_flash_write ---")
    assert issubclass(FlashMd5Mismatch, FlashWriteFailed)
    print("PASS: FlashMd5Mismatch inherits FlashWriteFailed")


def test_err_codes():
    """每个错误类都有独特的 ERR_CODE."""
    print("--- test_err_codes ---")
    codes_seen = set()
    classes = [
        (ESPROMError,          "ESP_ROM_ERROR_UNKNOWN"),
        (SyncFailed,           "ESP_ROM_SYNC_FAILED"),
        (CommandTimeout,       "ESP_ROM_TIMEOUT"),
        (BadResponse,          "ESP_ROM_BAD_RESPONSE"),
        (InvalidChecksum,      "ESP_ROM_INVALID_CHECKSUM"),
        (InvalidParam,         "ESP_ROM_INVALID_PARAM"),
        (FlashWriteFailed,     "ESP_ROM_FLASH_WRITE_FAILED"),
        (FlashEraseFailed,     "ESP_ROM_FLASH_ERASE_FAILED"),
        (FlashMd5Mismatch,     "ESP_ROM_MD5_MISMATCH"),
        (IoError,              "ESP_ROM_IO_ERROR"),
        (UnsupportedCommand,   "ESP_ROM_UNSUPPORTED_CMD"),
        (TargetNotResponding,  "ESP_ROM_TARGET_NO_RESP"),
        (InvalidState,         "ESP_ROM_INVALID_STATE"),
        (FileNotFoundError,    "ESP_ROM_FILE_NOT_FOUND"),
        (EmptyFirmwareFile,    "ESP_ROM_EMPTY_FIRMWARE"),
    ]
    for cls, expected_code in classes:
        actual = getattr(cls, 'ERR_CODE', None)
        assert actual == expected_code, \
            "%s: expected %s, got %s" % (cls.__name__, expected_code, actual)
        assert actual not in codes_seen, "duplicate ERR_CODE: %s" % actual
        codes_seen.add(actual)
    print("PASS: all 15 ERR_CODEs are unique and correct")


def test_str_format():
    """__str__ 返回 [ERR_CODE] message 格式."""
    print("--- test_str_format ---")
    e = SyncFailed("test message")
    s = str(e)
    assert "[ESP_ROM_SYNC_FAILED]" in s, "missing ERR_CODE in str: %r" % s
    assert "test message" in s, "missing message in str: %r" % s
    print("PASS: str format correct: %r" % s)


def test_except_order():
    """except 子类要在父类前, 否则永远捕获不到子类."""
    print("--- test_except_order ---")

    # 测试 1: FileNotFoundError 先于 FlashWriteFailed 捕获
    try:
        raise FileNotFoundError("no such file")
    except FileNotFoundError as e:
        assert "no such file" in str(e)
    except FlashWriteFailed:
        assert False, "should have caught FileNotFoundError first"

    # 测试 2: FlashMd5Mismatch 先于 FlashWriteFailed 捕获
    try:
        raise FlashMd5Mismatch("md5 diff")
    except FlashMd5Mismatch as e:
        assert "md5 diff" in str(e)
    except FlashWriteFailed:
        assert False, "should have caught FlashMd5Mismatch first"

    # 测试 3: 用 FlashWriteFailed 也能捕获子类 (向上兼容)
    try:
        raise FileNotFoundError("another")
    except FlashWriteFailed as e:
        # OK, 父类也能捕获
        assert "another" in str(e)

    print("PASS: except order works correctly")


def test_catch_all_via_base():
    """所有错误都能通过 ESPROMError 捕获 (向上兼容旧代码)."""
    print("--- test_catch_all_via_base ---")
    for cls in [SyncFailed, CommandTimeout, BadResponse, FlashWriteFailed,
                FileNotFoundError, EmptyFirmwareFile, FlashMd5Mismatch,
                IoError, UnsupportedCommand, TargetNotResponding, InvalidState,
                FlashEraseFailed, InvalidChecksum, InvalidParam]:
        try:
            raise cls("test")
        except ESPROMError as e:
            assert "test" in str(e)
            continue
        assert False, "%s not caught by ESPROMError" % cls.__name__
    print("PASS: all 14 specific errors caught via ESPROMError base class")


def test_message_attribute():
    """错误对象的 .message 属性正确."""
    print("--- test_message_attribute ---")
    e = CommandTimeout("waited 5s")
    assert e.message == "waited 5s"
    print("PASS: .message attribute accessible")


if __name__ == '__main__':
    print("\n=== Error Code Tests ===\n")
    test_inheritance()
    test_file_errors_inherit_flash_write()
    test_md5_mismatch_inherits_flash_write()
    test_err_codes()
    test_str_format()
    test_except_order()
    test_catch_all_via_base()
    test_message_attribute()
    print("\n=== ALL ERROR TESTS PASSED ===\n")
