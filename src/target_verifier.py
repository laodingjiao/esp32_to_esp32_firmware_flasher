"""
target_verifier.py
=================
烧后版本验证 (Post-Flash Version Verification).

借鉴 Machiel80 FlashBox 项目:
  烧完固件后, 让目标板复位运行新固件, 然后主机通过 UART 发送一个
  "version\\n" 命令, 期望目标板返回其版本号 (约定格式: "VERSION: x.y.z").

这样能确认:
  1. 烧的固件确实跑起来了 (不是死在 bootloader)
  2. 烧的固件版本与预期一致 (防止刷错文件)

工作原理:
  - flash_end + 复位后, 主机切换 UART 波特率到正常运行波特率 (115200)
  - 等 POST_FLASH_BOOT_DELAY_SEC 让目标板新固件启动完毕
  - 发送 POST_FLASH_VERSION_CMD (默认 b"version\\n")
  - 等 POST_FLASH_VERSION_TIMEOUT_SEC, 期间持续读 UART
  - 在响应中找 POST_FLASH_EXPECTED_VERSION_PREFIX (默认 b"VERSION:")
  - 若设了 POST_FLASH_EXPECTED_VERSION, 还要校验版本号本身

用法:
    verifier = TargetVerifier(uart)
    ok, version_str = verifier.verify_after_flash(target_ctrl)
    if ok:
        print("verified, version:", version_str)
    else:
        print("verification failed:", version_str)
"""

import utime
import config


class TargetVerifier:
    """烧后版本验证器.

    借鉴 Machiel80 FlashBox: 烧完后通过 UART 通信验证目标板固件版本.
    """

    def __init__(self, uart):
        """
        :param uart: machine.UART 实例 (烧录用同一根 UART, 烧完切换波特率)
        """
        self._uart = uart

    def verify_after_flash(self, target_ctrl):
        """烧完后执行验证流程.

        步骤:
          1. 通过 target_ctrl 复位目标板 (让它从 Flash 启动新固件)
          2. 等待 POST_FLASH_BOOT_DELAY_SEC 让新固件启动
          3. 通过 UART 发送 version 命令
          4. 等响应, 检查是否含期望版本前缀
          5. 若设了具体期望版本, 还要校验版本号本身

        :param target_ctrl: TargetController 实例 (用于复位目标板)
        :return: (success, version_str_or_error_msg) 元组
                 success=True 时 version_str 是读到的版本号字符串
                 success=False 时 version_str 是错误描述
        """
        if not config.POST_FLASH_VERIFY_ENABLE:
            if config.LOG_LEVEL >= 3:
                print("[DBG] post-flash verification disabled, skipping")
            return True, "disabled"

        # 1. 切换 UART 到正常运行波特率 (烧录用 INITIAL_BAUDRATE, 现在改回)
        # 通常运行时也是 115200, 但可配置
        try:
            self._uart.init(baudrate=config.INITIAL_BAUDRATE)
        except Exception as e:
            return False, "uart init failed: %s" % e

        # 2. 复位目标板 (BOOT=HIGH, EN 复位脉冲, 让它从 Flash 启动)
        try:
            target_ctrl.reset_target()
        except Exception as e:
            return False, "target reset failed: %s" % e

        # 3. 等待新固件启动 (bootloader 加载 + 应用初始化)
        if config.LOG_LEVEL >= 2:
            print("[INFO] post-flash: waiting %.1fs for new firmware to boot..."
                  % config.POST_FLASH_BOOT_DELAY_SEC)
        utime.sleep(config.POST_FLASH_BOOT_DELAY_SEC)

        # 4. 清空 UART 接收缓冲 (丢弃启动期间目标板输出的 boot 日志)
        try:
            self._uart.read()
        except Exception:
            pass

        # 5. 发送 version 命令
        if config.LOG_LEVEL >= 2:
            print("[INFO] post-flash: sending version cmd: %r"
                  % config.POST_FLASH_VERSION_CMD)
        try:
            self._uart.write(config.POST_FLASH_VERSION_CMD)
        except Exception as e:
            return False, "uart write failed: %s" % e

        # 6. 等响应, 持续读 POST_FLASH_VERSION_TIMEOUT_SEC
        response = self._read_until_timeout(
            config.POST_FLASH_VERSION_TIMEOUT_SEC)

        if config.LOG_LEVEL >= 3:
            print("[DBG] post-flash: raw response (%d bytes): %r"
                  % (len(response), response[:200]))

        # 7. 解析响应, 找版本前缀
        if not response:
            return False, "no response (target may not respond to version cmd)"

        return self._parse_version_response(response)

    def _read_until_timeout(self, timeout_sec):
        """持续读 UART 直到超时, 返回累积的所有字节."""
        deadline = utime.ticks_add(utime.ticks_ms(), int(timeout_sec * 1000))
        buf = bytearray()
        while utime.ticks_diff(deadline, utime.ticks_ms()) > 0:
            chunk = self._uart.read(64)
            if chunk:
                buf.extend(chunk)
            else:
                # 没数据, 让出 CPU 短暂时间
                utime.sleep_ms(50)
        return bytes(buf)

    def _parse_version_response(self, response):
        """解析版本响应, 返回 (success, version_str_or_error)."""
        prefix = config.POST_FLASH_EXPECTED_VERSION_PREFIX
        expected_ver = config.POST_FLASH_EXPECTED_VERSION

        # 找版本前缀
        if prefix is not None:
            idx = response.find(prefix)
            if idx < 0:
                return False, "version prefix %r not found in response" % prefix
            # 提取前缀后的内容 (到行尾)
            after_prefix = response[idx + len(prefix):]
            # 截取到第一个 \n 或 \r
            line_end = -1
            for ch in (b'\n', b'\r'):
                pos = after_prefix.find(ch)
                if pos >= 0 and (line_end < 0 or pos < line_end):
                    line_end = pos
            if line_end > 0:
                version_bytes = after_prefix[:line_end]
            else:
                version_bytes = after_prefix[:32]   # 限制长度
            # 去掉前导空白 (例如 " 1.0.0" 中的空格)
            version_str = version_bytes.strip().decode('ascii', 'replace')
        else:
            # 不要求前缀, 整个响应当作版本号
            version_str = response.strip().decode('ascii', 'replace')[:32]

        # 校验具体版本号
        if expected_ver is not None:
            expected_str = expected_ver.decode('ascii', 'replace')
            if version_str != expected_str:
                return False, "version mismatch: expected %r got %r" % (
                    expected_str, version_str)

        if config.LOG_LEVEL >= 2:
            print("[INFO] post-flash: version verified: %s" % version_str)
        return True, version_str

    def test_command(self, cmd, timeout_sec=None):
        """通用命令测试 (调试用): 发任意命令, 读回响应.

        :param cmd: 字节串, 例如 b"info\\n"
        :param timeout_sec: 等响应超时, 默认用 POST_FLASH_VERSION_TIMEOUT_SEC
        :return: 响应字节串
        """
        if timeout_sec is None:
            timeout_sec = config.POST_FLASH_VERSION_TIMEOUT_SEC
        try:
            self._uart.read()  # 清缓冲
            self._uart.write(cmd)
        except Exception as e:
            return b""
        return self._read_until_timeout(timeout_sec)
