"""
target_monitor.py
================
烧后日志监控 (Post-Flash Logging).

借鉴 helghast098 ESP32_flasher 项目:
  烧完固件后, 起一个 task 持续读 UART 转发到 stdout,
  便于实时看目标板新固件的日志输出 (REPL / printf 等).

适用场景:
  - 调试新刷的固件, 看启动日志 / 异常堆栈
  - 现场调试, 不方便接 USB 调试线时
  - 验证烧后版本号是否正确 (与 target_verifier 配合)

注意:
  - 监控期间主机"卡住"在这个 task, 直到:
    * 达到 POST_FLASH_MONITOR_DURATION 秒
    * 收到目标板发送的退出关键字 (POST_FLASH_MONITOR_EXIT_CMD)
    * 用户主动中断 (Ctrl+C, 但 MicroPython 上不一定支持)
  - 默认配置关闭 (POST_FLASH_MONITOR_ENABLE = False), 因持续模式不需要
  - 启用方法: 编辑 config.py 把 POST_FLASH_MONITOR_ENABLE = True

用法 (在 main.py 中):
    monitor = TargetMonitor(uart)
    monitor.start_monitoring(led)  # led 用于闪烁状态指示
"""

import utime
import config


class TargetMonitor:
    """烧后日志监控器.

    借鉴 helghast098 ESP32_flasher 的 monitor_target() task:
    持续读 UART 转发到 stdout, 实时看目标板日志.
    """

    def __init__(self, uart):
        """
        :param uart: machine.UART 实例 (与烧录用同一根 UART)
        """
        self._uart = uart

    def start_monitoring(self, led=None):
        """开始监控目标板日志.

        流程:
          1. 切换 UART 到正常运行波特率 (115200)
          2. 进入循环:
             a. 读 UART 数据
             b. 转发到 stdout
             c. 检查是否含退出关键字
             d. LED tick 维持状态闪烁
             e. 检查超时
          3. 退出 (超时 / 收到关键字)

        :param led: LedStatus 实例 (可选, 用于状态闪烁)
        :return: 监控结束原因 ("timeout" / "exit_cmd" / "error")
        """
        if not config.POST_FLASH_MONITOR_ENABLE:
            if config.LOG_LEVEL >= 2:
                print("[INFO] monitoring disabled, skipping")
            return "disabled"

        # 1. 切换 UART 到正常运行波特率
        try:
            self._uart.init(baudrate=config.INITIAL_BAUDRATE)
        except Exception as e:
            if config.LOG_LEVEL >= 1:
                print("[ERR] monitor: uart init failed: %s" % e)
            return "error"

        # 2. 进入 LED 监控状态
        if led is not None:
            led.set_state("monitoring")

        if config.LOG_LEVEL >= 1:
            print("[INFO] === TARGET MONITORING ===")
            print("[INFO] monitoring for %.1fs, exit on %r"
                  % (config.POST_FLASH_MONITOR_DURATION_SEC,
                     config.POST_FLASH_MONITOR_EXIT_CMD))
            print("[INFO] (target logs below)")
            print("-" * 40)

        # 3. 持续读 + 转发
        deadline = utime.ticks_add(
            utime.ticks_ms(),
            int(config.POST_FLASH_MONITOR_DURATION_SEC * 1000)
        )
        exit_cmd = config.POST_FLASH_MONITOR_EXIT_CMD
        exit_reason = "timeout"

        while True:
            # 检查超时
            if utime.ticks_diff(deadline, utime.ticks_ms()) <= 0:
                exit_reason = "timeout"
                break

            # 读 UART
            try:
                chunk = self._uart.read(128)
            except Exception as e:
                if config.LOG_LEVEL >= 1:
                    print("[ERR] monitor: uart read failed: %s" % e)
                exit_reason = "error"
                break

            # 转发到 stdout
            if chunk:
                try:
                    # MicroPython 的 sys.stdout.write 不接受 bytes, 要 decode
                    # 用 'latin-1' 避免非 ASCII 字节抛异常
                    text = chunk.decode('latin-1', 'replace')
                    print(text, end='')
                except Exception:
                    # 直接打印 hex 兜底
                    print(ubinascii.hexlify(chunk))

                # 检查退出关键字
                if exit_cmd and exit_cmd in chunk:
                    exit_reason = "exit_cmd"
                    break

            # LED tick (维持闪烁)
            if led is not None:
                led.tick()

            # 短暂让出 CPU
            utime.sleep_ms(10)

        # 4. 退出
        print()
        print("-" * 40)
        if config.LOG_LEVEL >= 1:
            print("[INFO] monitoring ended: %s" % exit_reason)

        return exit_reason
