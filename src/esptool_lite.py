"""
esptool_lite.py
==============
ESP32 ROM bootloader 协议的扩展模块, 基于 micropython-lib 官方 espflash.

复用关系:
  - SLIP 编解码、命令收发、SYNC、Flash 读写、MD5 校验、复位 → 直接用 espflash.ESPFlash
  - Stub Loader 加载 (mem_begin/mem_block/mem_finish/run_stub) → 本模块扩展 (借鉴 esptool.py)
  - 芯片型号检测 (GET_SECURITY_INFO / magic value) → 本模块扩展 (借鉴 esptool.py)
  - 完整错误码体系 (15 个细分错误类) → 本模块扩展 (借鉴 espressif/esp-serial-flasher)

简化效果:
  - 原版 (重复造轮子): 980 行
  - 新版 (复用 espflash + 仅扩展): ~350 行
  - 减少 ~630 行重复代码

借鉴来源:
  - micropython-lib/espflash.py (MIT): SLIP / SYNC / flash_begin/data/end / MD5 / reboot
  - esptool.py (Apache 2.0): stub loader 协议 (mem_begin/mem_block/mem_finish/run_stub)
  - espressif/esp-serial-flasher (Apache 2.0): 完整错误码体系
"""

import ustruct as struct
import utime
import uhashlib
import ubinascii
import ujson
import config
# 复用 micropython-lib 官方模块
from espflash import ESPFlash, _CMD_SYNC, _CMD_CHANGE_BAUDRATE
from espflash import _CMD_ESP_READ_REG
# 新增: espflash 没有 MEM 命令常量和 GET_SECURITY_INFO, 我们自己定义
from espflash import _CMD_SPI_FLASH_BEGIN, _CMD_SPI_FLASH_DATA, _CMD_SPI_FLASH_END
from espflash import _CMD_SPI_FLASH_MD5


# ============================================================================
# 错误码体系 (Error Code Hierarchy)
# ============================================================================
# 借鉴 espressif/esp-serial-flasher 的 esp_loader_error_t (30+ 错误码)
# 这里实现 MicroPython 版本适用的 15 个细分错误, 方便上层针对性处理.
# 所有错误都继承自 ESPROMError, 兼容现有 except ESPROMError 写法.


class ESPROMError(Exception):
    """ROM 协议错误基类.

    所有具体错误的父类. 上层代码可直接 `except ESPROMError` 捕获全部协议错误,
    也可针对单个错误子类做精细化处理 (重试 / 跳过 / 报警).
    """
    # 错误码 (类似 C 库的 enum), 用于日志/统计
    ERR_CODE = "ESP_ROM_ERROR_UNKNOWN"

    def __init__(self, msg="", *args):
        super().__init__(msg, *args)
        self.message = msg

    def __str__(self):
        return "[%s] %s" % (self.ERR_CODE, self.message)


# ---- 连接 / 握手类错误 ----
class SyncFailed(ESPROMError):
    """SYNC 握手失败."""
    ERR_CODE = "ESP_ROM_SYNC_FAILED"


class CommandTimeout(ESPROMError):
    """命令超时 (等待响应超时)."""
    ERR_CODE = "ESP_ROM_TIMEOUT"


# ---- 协议帧格式类错误 ----
class BadResponse(ESPROMError):
    """响应包格式错误或状态码非 0."""
    ERR_CODE = "ESP_ROM_BAD_RESPONSE"


class InvalidChecksum(ESPROMError):
    """校验和不匹配."""
    ERR_CODE = "ESP_ROM_INVALID_CHECKSUM"


class InvalidParam(ESPROMError):
    """参数错误."""
    ERR_CODE = "ESP_ROM_INVALID_PARAM"


# ---- Flash 操作类错误 ----
class FlashWriteFailed(ESPROMError):
    """写入 Flash 失败 (含 MD5 校验不过)."""
    ERR_CODE = "ESP_ROM_FLASH_WRITE_FAILED"


class FlashEraseFailed(ESPROMError):
    """擦除 Flash 失败."""
    ERR_CODE = "ESP_ROM_FLASH_ERASE_FAILED"


class FlashMd5Mismatch(FlashWriteFailed):
    """MD5 校验失败 (本地 vs ROM 端不一致)."""
    ERR_CODE = "ESP_ROM_MD5_MISMATCH"


# ---- 通信类错误 ----
class IoError(ESPROMError):
    """UART I/O 错误 (硬件层)."""
    ERR_CODE = "ESP_ROM_IO_ERROR"


class UnsupportedCommand(ESPROMError):
    """ROM 不支持此命令."""
    ERR_CODE = "ESP_ROM_UNSUPPORTED_CMD"


# ---- 状态类错误 ----
class TargetNotResponding(ESPROMError):
    """目标板无响应."""
    ERR_CODE = "ESP_ROM_TARGET_NO_RESP"


class InvalidState(ESPROMError):
    """状态机错误 (调用顺序错)."""
    ERR_CODE = "ESP_ROM_INVALID_STATE"


# ---- 文件类错误 ----
class FileNotFoundError(FlashWriteFailed):
    """固件文件不存在或无法打开."""
    ERR_CODE = "ESP_ROM_FILE_NOT_FOUND"


class EmptyFirmwareFile(FlashWriteFailed):
    """固件文件为空 (0 字节)."""
    ERR_CODE = "ESP_ROM_EMPTY_FIRMWARE"


# ============================================================================
# ROM 协议常量 (扩展 espflash 没有的)
# ============================================================================
# espflash 已定义: _CMD_SYNC/_CMD_CHANGE_BAUDRATE/_CMD_ESP_READ_REG/...
# 我们额外定义 MEM_* (stub loader 用) 和 GET_SECURITY_INFO (芯片检测用)

_CMD_MEM_BEGIN        = 0x05   # 上传数据到 RAM (stub loader 用)
_CMD_MEM_END          = 0x06   # 完成 RAM 上传 + 跳转
_CMD_MEM_DATA         = 0x07   # 发送 RAM 数据块
_CMD_GET_SECURITY_INFO = 0x14  # 获取芯片安全信息 (含 chip_id, 用于新芯片识别)
_CMD_ERASE_FLASH      = 0xD0   # 整片擦除


# ============================================================================
# StubFlasher: 扩展 ESPFlash, 加入 stub loader 和芯片检测
# ============================================================================

class StubFlasher(ESPFlash):
    """扩展 micropython-lib 的 ESPFlash, 加入:
      1. Stub Loader 加载 (借鉴 esptool.py 的 run_stub)
      2. 芯片型号检测 (借鉴 esptool.py 的 detect_chip)
      3. 完整错误码体系 (借鉴 espressif/esp-serial-flasher)

    用法:
        tool = StubFlasher(reset_pin, gpio0_pin, uart)
        tool.bootloader()                # 进入下载模式 (复用 ESPFlash)
        chip_key, name = tool.detect_chip()      # 自动检测芯片 (扩展)
        tool.load_stub(chip_key=chip_key)        # 加载对应 stub (扩展)
        tool.set_baudrate(460800)        # 切换到高速 (复用 ESPFlash)
        tool.flash_write_file(path)      # 烧录 (复用 ESPFlash)
        tool.flash_verify_file(path)     # MD5 校验 (复用 ESPFlash)
        tool.reboot()                    # 复位 (复用 ESPFlash)
    """

    def __init__(self, reset, gpio0, uart, log_enabled=False):
        super().__init__(reset, gpio0, uart, log_enabled)
        # stub loader 是否已加载 (借鉴 esptool.py 的 IS_STUB 标志)
        self.is_stub = False

    # ===========================================================
    # 公开: 芯片型号检测 (借鉴 esptool.py 的 detect_chip)
    # ===========================================================
    def get_security_info(self):
        """获取芯片安全信息 (借鉴 esptool.py 的 get_security_info).

        ESP32-S3 及以后的新芯片支持此命令, 返回 12+ 字节:
          - byte 0:    flags
          - byte 1-3:  flash_crypt_cnt (3 byte)
          - byte 4-11: key_purposes (8 byte)
          - byte 12-15: chip_id (4 byte, 关键字段!)
        """
        # 直接调 ESPFlash._command, 但要处理新命令
        # espflash 的 _command 内部用 _CMD_SYNC 等, 我们传 _CMD_GET_SECURITY_INFO
        val, data = self._command(_CMD_GET_SECURITY_INFO, b'')
        if len(data) < 12:
            raise BadResponse("GET_SECURITY_INFO: response too short %d" % len(data))

        flags = data[0]
        flash_crypt_cnt = data[1] | (data[2] << 8) | (data[3] << 16)
        key_purposes = struct.unpack('<8B', data[4:12])
        chip_id = struct.unpack('<I', data[12:16])[0] if len(data) >= 16 else None

        return {
            'flags': flags,
            'flash_crypt_cnt': flash_crypt_cnt,
            'key_purposes': key_purposes,
            'chip_id': chip_id,
        }

    def detect_chip(self):
        """检测目标芯片型号 (借鉴 esptool.py 的 detect_chip).

        策略 (与 esptool 完全一致):
          1. 先试 GET_SECURITY_INFO 命令 (新芯片 S3+ 支持)
          2. 若上面失败, 用 READ_REG 读 0x40001000 的 magic value (老芯片用)

        :return: (chip_key, chip_name) 元组
        """
        if config.LOG_LEVEL >= 2:
            print("[INFO] detecting target chip...")

        # 策略 1: 试 GET_SECURITY_INFO (新芯片支持)
        try:
            info = self.get_security_info()
            chip_id = info.get('chip_id')
            if config.LOG_LEVEL >= 3:
                print("[DBG] GET_SECURITY_INFO: chip_id=%s flags=0x%X"
                      % (chip_id, info['flags']))

            if chip_id is not None:
                for cid, (key, name) in config.CHIP_ID_VALUES.items():
                    if chip_id == cid:
                        if config.LOG_LEVEL >= 2:
                            print("[INFO] detected (via chip_id): %s" % name)
                        return key, name
                if config.LOG_LEVEL >= 1:
                    print("[WARN] unknown chip_id %d, falling back to ROM direct" % chip_id)
                return None, "Unknown (chip_id=%d)" % chip_id
        except Exception as e:
            if config.LOG_LEVEL >= 3:
                print("[DBG] GET_SECURITY_INFO failed: %s (likely old chip)" % e)

        # 策略 2: 读 magic value (老芯片路径)
        try:
            # 不能直接用 ESPFlash._command 发 READ_REG, 因为 ESPFlash 假设响应最后
            # 4 字节是 status, 但 READ_REG 的响应没有 status (最后 4 字节是寄存器值).
            # 所以我们用 _write_slip + _read_slip 手动收发.
            import ustruct as _struct
            pkt = _struct.pack('<BBHI', 0, _CMD_ESP_READ_REG,
                               4, 0) + _struct.pack('<I', config.CHIP_DETECT_MAGIC_REG)
            self._write_slip(pkt)
            resp = self._read_slip()
            if resp is None or len(resp) < 8:
                raise BadResponse("READ_REG: no response")
            # 响应格式: [direction(1)][cmd(1)][size(2)][value/status(4)][data...]
            # 对 READ_REG, value 字段(4B)是 status(=0), 寄存器值在 data 部分(resp[8:])
            direction, resp_cmd, resp_size = struct.unpack('<BBH', resp[:4])
            if direction != 1:
                raise BadResponse("READ_REG: direction not response")
            if resp_cmd != _CMD_ESP_READ_REG:
                raise BadResponse("READ_REG: cmd mismatch")
            if len(resp) < 12:
                raise BadResponse("READ_REG: response too short for register value")
            magic = struct.unpack('<I', resp[8:12])[0]
            if config.LOG_LEVEL >= 3:
                print("[DBG] magic value at 0x%08X: 0x%08X"
                      % (config.CHIP_DETECT_MAGIC_REG, magic))

            for mv, (key, name) in config.CHIP_MAGIC_VALUES.items():
                if magic == mv:
                    if config.LOG_LEVEL >= 2:
                        print("[INFO] detected (via magic value): %s" % name)
                    return key, name

            if config.LOG_LEVEL >= 1:
                print("[WARN] unknown magic value 0x%08X, falling back to ROM direct" % magic)
            return None, "Unknown (magic=0x%08X)" % magic

        except Exception as e:
            raise SyncFailed(
                "chip detection failed: GET_SECURITY_INFO and READ_REG both failed: %s" % e)

    def get_stub_file_for_chip(self, chip_key):
        """根据芯片型号获取对应 stub JSON 文件路径."""
        if chip_key is None:
            raise FileNotFoundError("chip not detected, cannot determine stub file")

        stub_filename = config.CHIP_STUB_FILES.get(chip_key)
        if stub_filename is None:
            raise FileNotFoundError(
                "no stub binary available for chip '%s' (will use ROM direct)" % chip_key)

        return config.STUB_LOADER_DIR.rstrip('/') + '/' + stub_filename

    # ===========================================================
    # Stub Loader 支持 (借鉴 esptool.py 的 run_stub)
    # ===========================================================
    def mem_begin(self, size, blocks, block_size, offset):
        """通知 ROM 即将上传数据到 RAM (借鉴 esptool.py ESPLoader.mem_begin)."""
        data = struct.pack('<IIII', size, blocks, block_size, offset)
        if config.LOG_LEVEL >= 3:
            print("[DBG] MEM_BEGIN: size=%d blocks=%d block_size=0x%X offset=0x%08X"
                  % (size, blocks, block_size, offset))
        self._command(_CMD_MEM_BEGIN, data)

    def mem_block(self, data, seq):
        """发送一个数据块到 RAM (借鉴 esptool.py ESPLoader.mem_block)."""
        checksum = 0
        for b in data:
            checksum ^= b
        payload = struct.pack('<IIII', len(data), seq, 0, 0) + data
        self._command(_CMD_MEM_DATA, payload, checksum)

    def mem_finish(self, entrypoint):
        """完成 RAM 上传, 让目标板跳转到 entrypoint (借鉴 esptool.py ESPLoader.mem_finish)."""
        flag = 0 if entrypoint else 1
        data = struct.pack('<II', flag, entrypoint)
        try:
            self._command(_CMD_MEM_END, data)
        except Exception:
            # MEM_END 后 stub 立即接管 UART, ROM 可能不再回响应, 超时正常
            if config.LOG_LEVEL >= 3:
                print("[DBG] MEM_END timeout (normal, stub is taking over)")

    def _load_stub_segment(self, data, offset):
        """上传一个段 (text 或 data) 到 RAM (借鉴 esptool.py _upload_segment)."""
        block_size = config.ESP_RAM_BLOCK_SIZE
        length = len(data)
        blocks = (length + block_size - 1) // block_size
        if config.LOG_LEVEL >= 2:
            print("[INFO]   uploading %d bytes (in %d blocks) to 0x%08X"
                  % (length, blocks, offset))
        self.mem_begin(length, blocks, block_size, offset)
        for seq in range(blocks):
            start = seq * block_size
            end = min(start + block_size, length)
            self.mem_block(data[start:end], seq)

    def load_stub(self, stub_file=None, chip_key=None):
        """加载 stub loader 到目标板 RAM (借鉴 esptool.py run_stub).

        完整流程:
          1. (可选) 调用 detect_chip() 自动识别芯片型号, 选对应 stub JSON
          2. 读 stub JSON 文件 (含 text/data 段 + entry 地址)
          3. 上传 text 段到 text_start (地址因芯片而异)
          4. 上传 data 段到 data_start
          5. 调 mem_finish(entry) 让 stub 跳转执行
          6. 等 stub 发 b'OHAI' 就绪信号

        :param stub_file: 显式指定 stub JSON 路径 (覆盖自动检测)
        :param chip_key: 显式指定芯片型号 (如 "esp32s3"), 覆盖自动检测
        :return: True 表示 stub 加载成功
        :raises: SyncFailed 如果 stub 没响应 OHAI
        :raises: FileNotFoundError 如果 stub 文件不存在或该芯片无 stub
        """
        # 自动检测芯片型号 (如果未指定)
        if stub_file is None:
            if chip_key is None:
                try:
                    chip_key, chip_name = self.detect_chip()
                except SyncFailed as e:
                    raise SyncFailed("auto-detect chip failed: %s" % e)
                if chip_key is None:
                    raise FileNotFoundError(
                        "could not detect chip, cannot determine stub file")

            stub_file = self.get_stub_file_for_chip(chip_key)
            if config.LOG_LEVEL >= 2:
                print("[INFO] using stub file for %s: %s"
                      % (chip_key, stub_file.split('/')[-1]))

        # 1. 读 stub JSON 文件
        try:
            with open(stub_file, 'r') as f:
                stub_info = ujson.load(f)
        except OSError as e:
            raise FileNotFoundError(
                "stub loader file not found: %s (%s)" % (stub_file, e))

        # 解析 JSON 字段
        entry      = stub_info['entry']
        text_b64   = stub_info['text']
        text_start = stub_info['text_start']
        data_b64   = stub_info.get('data', '')
        data_start = stub_info.get('data_start', 0)

        # base64 解码
        text_bytes = ubinascii.a2b_base64(text_b64)
        data_bytes = ubinascii.a2b_base64(data_b64) if data_b64 else b''

        if config.LOG_LEVEL >= 2:
            print("[INFO] loading stub: entry=0x%08X text=%dB data=%dB"
                  % (entry, len(text_bytes), len(data_bytes)))

        # 2. 上传 text 段
        if text_bytes:
            self._load_stub_segment(text_bytes, text_start)

        # 3. 上传 data 段
        if data_bytes:
            self._load_stub_segment(data_bytes, data_start)

        # 4. 让 stub 跳转执行
        if config.LOG_LEVEL >= 2:
            print("[INFO] stub uploaded, jumping to entry 0x%08X..." % entry)
        self.mem_finish(entry)

        # 5. 等 stub 发 OHAI 就绪信号
        try:
            ohai = self._read_until_ohai(int(config.STUB_OHAI_TIMEOUT * 1000))
        except Exception:
            raise SyncFailed(
                "stub loader did not send OHAI within %ds (timeout)"
                % config.STUB_OHAI_TIMEOUT)
        if b'OHAI' not in ohai:
            raise SyncFailed(
                "stub loader did not send OHAI: got %r" % ohai[:32])

        self.is_stub = True
        if config.LOG_LEVEL >= 2:
            print("[INFO] stub loader running (OHAI received)")
        return True

    def _read_until_ohai(self, timeout_ms):
        """读 UART 直到遇到 b'OHAI' (stub 就绪信号)."""
        deadline = utime.ticks_add(utime.ticks_ms(), timeout_ms)
        buf = bytearray()
        while utime.ticks_diff(deadline, utime.ticks_ms()) > 0:
            b = self.uart.read(1)
            if b:
                buf.extend(b)
                if len(buf) >= 4 and bytes(buf[-4:]) == b'OHAI':
                    return bytes(buf)
            else:
                utime.sleep_ms(1)
        return bytes(buf)

    # ===========================================================
    # 高层 API: 一键烧录 (扩展 ESPFlash, 加 stub 加速 + MD5 校验)
    # ===========================================================
    def flash_file(self, file_path, offset=0x0, verify=True):
        """烧录一个文件到 flash.

        复用 ESPFlash 的 flash_write_file + flash_verify_file, 但加入:
          - 完整错误处理 (用我们的 15 个错误类)
          - MD5 校验失败抛 FlashMd5Mismatch (而非通用 Exception)
          - 文件不存在抛 FileNotFoundError
          - 空文件抛 EmptyFirmwareFile

        :param file_path: 固件文件路径
        :param offset: 写入起始地址 (ESP32_GENERIC 全镜像从 0 开始)
        :param verify: 是否烧完后做 MD5 校验
        """
        # 检查文件
        try:
            f = open(file_path, 'rb')
            f.seek(0, 2)
            total_size = f.tell()
            f.close()
        except OSError as e:
            raise FileNotFoundError("open %s failed: %s" % (file_path, e))

        if total_size == 0:
            raise EmptyFirmwareFile("firmware file is empty: %s" % file_path)

        if config.LOG_LEVEL >= 2:
            print("[INFO] flashing %s (%d bytes) at offset 0x%X..."
                  % (file_path, total_size, offset))

        # 调用 ESPFlash 的 flash_write_file (会处理 begin/data 循环 + 内置 MD5 累积)
        try:
            self.flash_write_file(file_path, blksize=config.FLASH_BLOCK_SIZE)
        except Exception as e:
            # 转 FlashWriteFailed
            raise FlashWriteFailed("flash write failed: %s" % e)

        # MD5 校验 (可选)
        if verify:
            self._verify_md5(file_path, offset, total_size)

        if config.LOG_LEVEL >= 2:
            print("[INFO] flash write complete")

    def _verify_md5(self, file_path, offset, total_size):
        """通过 ROM 计算 flash 指定区域 MD5, 与本地文件 MD5 对比.

        复用 ESPFlash 的 flash_verify_file, 但失败时抛 FlashMd5Mismatch.
        """
        if config.LOG_LEVEL >= 2:
            print("[INFO] verifying via MD5...")
        try:
            self.flash_verify_file(file_path, offset=offset)
        except Exception as e:
            # 区分 MD5 不匹配 vs 其他错误
            msg = str(e)
            if "verification failed" in msg.lower() or "md5" in msg.lower():
                raise FlashMd5Mismatch("MD5 mismatch: %s" % e)
            raise FlashWriteFailed("verify failed: %s" % e)

        if config.LOG_LEVEL >= 2:
            print("[INFO] MD5 verify OK")
