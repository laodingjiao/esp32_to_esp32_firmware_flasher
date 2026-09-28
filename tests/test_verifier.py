"""
test_verifier.py
===============
测试 target_verifier.py 的版本响应解析逻辑.

不依赖真实 UART, 用 MockUART 模拟目标板响应.

借鉴 Machiel80 FlashBox 的烧后版本验证机制:
  烧完发 "version\n" 命令, 期望目标板返回 "VERSION: x.y.z" 格式响应.

运行: python3 test_verifier.py
"""

import sys
import types
import struct

import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# ---- stub: utime ----
utime_mod = types.ModuleType('utime')
import time
utime_mod.ticks_ms = lambda: int(time.time() * 1000)
utime_mod.ticks_add = lambda t, d: t + d
utime_mod.ticks_diff = lambda a, b: a - b
utime_mod.sleep_ms = lambda ms: time.sleep(ms / 1000.0)
utime_mod.sleep = lambda s: time.sleep(s)
sys.modules['utime'] = utime_mod

# ---- stub: config ----
config_mod = types.ModuleType('config')
config_mod.LOG_LEVEL = 0
config_mod.POST_FLASH_VERIFY_ENABLE = True
config_mod.POST_FLASH_BOOT_DELAY_SEC = 0.01  # 测试时缩短
config_mod.POST_FLASH_VERSION_CMD = b"version\n"
config_mod.POST_FLASH_VERSION_TIMEOUT_SEC = 1.0
config_mod.POST_FLASH_EXPECTED_VERSION_PREFIX = b"VERSION:"
config_mod.POST_FLASH_EXPECTED_VERSION = None
config_mod.INITIAL_BAUDRATE = 115200
sys.modules['config'] = config_mod

# stub: machine (for uart.init)
machine_mod = types.ModuleType('machine')
sys.modules['machine'] = machine_mod

# 现在可以 import 被测模块
import target_verifier


class MockUART:
    """模拟 UART, 按预设响应返回数据.

    模拟真实 UART 的流式行为: 数据分多次返回, 每次最多 n 字节.
    这模仿了硬件 UART 在收到完整响应前可能多次 read 都返回 None 的场景.
    """

    def __init__(self, response_data=b""):
        self._response = response_data
        self._read_pos = 0
        self._written = b""
        # 模拟 UART 数据到达延迟: 前几次 read 返回 None, 之后才有数据
        self._empty_reads_before_data = 3

    def init(self, **kw):
        pass

    def write(self, data):
        self._written += data
        return len(data)

    def read(self, n=None):
        if n is None:
            n = 64
        # 前几次返回 None 模拟数据还没到
        if self._empty_reads_before_data > 0:
            self._empty_reads_before_data -= 1
            return None
        # 数据已读完
        if self._read_pos >= len(self._response):
            return None
        # 返回一段
        end = min(self._read_pos + n, len(self._response))
        chunk = self._response[self._read_pos:end]
        self._read_pos = end
        return chunk


class MockTargetCtrl:
    """模拟 target_controller, 复位方法不做任何事."""

    def reset_target(self):
        pass


def test_verify_disabled():
    """配置禁用时返回 (True, 'disabled')."""
    print("--- test_verify_disabled ---")
    config_mod.POST_FLASH_VERIFY_ENABLE = False
    try:
        v = target_verifier.TargetVerifier(MockUART())
        ok, msg = v.verify_after_flash(MockTargetCtrl())
        assert ok is True
        assert "disabled" in msg
        print("PASS")
    finally:
        config_mod.POST_FLASH_VERIFY_ENABLE = True


def test_verify_with_correct_prefix():
    """目标板返回含 VERSION: 前缀, 验证通过."""
    print("--- test_verify_with_correct_prefix ---")
    uart = MockUART(b"boot log...\nVERSION: 1.2.3\nmore output\n")
    v = target_verifier.TargetVerifier(uart)
    ok, version = v.verify_after_flash(MockTargetCtrl())
    assert ok, "should succeed: %s" % version
    assert version == "1.2.3", "expected '1.2.3', got %r" % version
    # 验证 version 命令确实发了
    assert b"version\n" in uart._written, "version cmd not sent"
    print("PASS: extracted version '1.2.3'")


def test_verify_no_response():
    """目标板无响应, 验证失败."""
    print("--- test_verify_no_response ---")
    uart = MockUART(b"")   # 空响应
    v = target_verifier.TargetVerifier(uart)
    ok, msg = v.verify_after_flash(MockTargetCtrl())
    assert not ok, "should fail with no response"
    assert "no response" in msg
    print("PASS: no response correctly rejected")


def test_verify_wrong_prefix():
    """目标板返回不含 VERSION: 前缀, 验证失败."""
    print("--- test_verify_wrong_prefix ---")
    uart = MockUART(b"unknown output without prefix\n")
    v = target_verifier.TargetVerifier(uart)
    ok, msg = v.verify_after_flash(MockTargetCtrl())
    assert not ok, "should fail without prefix"
    assert "version prefix" in msg
    print("PASS: missing prefix correctly rejected")


def test_verify_specific_version_match():
    """期望具体版本号, 实际匹配, 验证通过."""
    print("--- test_verify_specific_version_match ---")
    config_mod.POST_FLASH_EXPECTED_VERSION = b"1.0.0"
    try:
        uart = MockUART(b"VERSION: 1.0.0\n")
        v = target_verifier.TargetVerifier(uart)
        ok, version = v.verify_after_flash(MockTargetCtrl())
        assert ok, "should succeed: %s" % version
        assert version == "1.0.0"
        print("PASS: specific version matched")
    finally:
        config_mod.POST_FLASH_EXPECTED_VERSION = None


def test_verify_specific_version_mismatch():
    """期望具体版本号, 实际不匹配, 验证失败."""
    print("--- test_verify_specific_version_mismatch ---")
    config_mod.POST_FLASH_EXPECTED_VERSION = b"2.0.0"
    try:
        uart = MockUART(b"VERSION: 1.0.0\n")
        v = target_verifier.TargetVerifier(uart)
        ok, msg = v.verify_after_flash(MockTargetCtrl())
        assert not ok, "should fail version mismatch"
        assert "version mismatch" in msg
        print("PASS: version mismatch correctly rejected")
    finally:
        config_mod.POST_FLASH_EXPECTED_VERSION = None


def test_verify_handles_carriage_return():
    """响应含 \\r\\n 时正确提取版本号."""
    print("--- test_verify_handles_carriage_return ---")
    uart = MockUART(b"VERSION: 1.5.0\r\n")
    v = target_verifier.TargetVerifier(uart)
    ok, version = v.verify_after_flash(MockTargetCtrl())
    assert ok
    assert version == "1.5.0", "expected '1.5.0', got %r" % version
    print("PASS: handles \\r\\n correctly")


def test_verify_no_prefix_mode():
    """POST_FLASH_EXPECTED_VERSION_PREFIX = None 时, 整个响应当版本号."""
    print("--- test_verify_no_prefix_mode ---")
    saved = config_mod.POST_FLASH_EXPECTED_VERSION_PREFIX
    config_mod.POST_FLASH_EXPECTED_VERSION_PREFIX = None
    try:
        uart = MockUART(b"v1.7.2\n")
        v = target_verifier.TargetVerifier(uart)
        ok, version = v.verify_after_flash(MockTargetCtrl())
        assert ok
        assert "v1.7.2" in version
        print("PASS: no-prefix mode works, got %r" % version)
    finally:
        config_mod.POST_FLASH_EXPECTED_VERSION_PREFIX = saved


def test_test_command_method():
    """通用 test_command 方法可发任意命令读回响应."""
    print("--- test_test_command_method ---")
    uart = MockUART(b"INFO: build 123\n")
    v = target_verifier.TargetVerifier(uart)
    resp = v.test_command(b"info\n")
    assert b"INFO: build 123" in resp
    print("PASS: test_command works")


if __name__ == '__main__':
    print("\n=== TargetVerifier Tests ===\n")
    test_verify_disabled()
    test_verify_with_correct_prefix()
    test_verify_no_response()
    test_verify_wrong_prefix()
    test_verify_specific_version_match()
    test_verify_specific_version_mismatch()
    test_verify_handles_carriage_return()
    test_verify_no_prefix_mode()
    test_test_command_method()
    print("\n=== ALL VERIFIER TESTS PASSED ===\n")
