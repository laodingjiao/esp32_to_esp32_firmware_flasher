"""
main.py
======
ESP32 固件烧录器 —— 主程序.

启动流程:
  1. 初始化: 加载 config, 配置 UART/GPIO/LED
  2. 启动检测循环:
     a. 读 GPIO 选择槽位, 确定要烧的固件
     b. 检测目标板是否连接
     c. 若连接 + 选中固件 → 自动烧录
     d. 烧录完成 → 复位目标板 → (可选)烧后验证 → (可选)日志监控 → 等待 → 回到 a
  3. 异常处理: 失败重试 N 次, LED 进入 error 状态

状态机:
  idle → searching → entering_boot → syncing → erasing → flashing
        → verifying → (复位目标板)
        → (可选) post_verifying (发 version 命令读回版本号)
        → (可选) monitoring (被动监听目标板日志)
        → success → idle
  任意阶段失败 → error → (等待重试) → idle

借鉴 Machiel80 FlashBox (post_verifying 阶段):
  烧完后让目标板运行新固件, 通过 UART 发 "version" 命令读回版本号验证.

借鉴 helghast098 ESP32_flasher (monitoring 阶段):
  烧完后起 task 持续读 UART 转发到 stdout, 实时看目标板新固件日志.

LED 频率对应状态见 config.LED_FREQ_*
"""

import utime
import sys
from machine import UART, Pin
import config
from led_state import LedStatus
from firmware_selector import FirmwareSelector
from target_controller import TargetController
# 复用 micropython-lib 的 ESPFlash (底层协议)
# 我们的 StubFlasher 继承 ESPFlash, 加入 stub loader + 芯片检测 + 错误码
from esptool_lite import (
    StubFlasher,
    ESPROMError,                # 基类, 捕获所有协议错误
    SyncFailed,                 # SYNC 握手失败
    CommandTimeout,             # 命令超时
    BadResponse,                # 响应包格式错误
    InvalidChecksum,            # 校验和不匹配
    InvalidParam,               # 参数错误
    FlashWriteFailed,           # 写入失败 (通用)
    FlashEraseFailed,           # 擦除失败
    FlashMd5Mismatch,           # MD5 不匹配 (FlashWriteFailed 子类)
    IoError,                    # UART I/O 错误
    UnsupportedCommand,         # ROM 不支持此命令
    TargetNotResponding,        # 目标板无响应
    InvalidState,               # 状态机错误
    FileNotFoundError,          # 固件文件不存在 (FlashWriteFailed 子类)
    EmptyFirmwareFile,          # 固件文件空 (FlashWriteFailed 子类)
)
# 借鉴 Machiel80: 烧后版本验证
from target_verifier import TargetVerifier
# 借鉴 helghast098: 烧后日志监控
from target_monitor import TargetMonitor


# ============================================================================
# 全局对象 (在 main() 中初始化)
# ============================================================================
led = None
selector = None
target_ctrl = None
uart = None


def init_hardware():
    """初始化所有硬件外设."""
    global led, selector, target_ctrl, uart

    if config.LOG_LEVEL >= 1:
        config.print_banner()

    # 1. 状态 LED
    led = LedStatus()
    led.set_state("idle")

    # 2. 固件选择器 (读 GPIO 输入)
    selector = FirmwareSelector()

    # 3. 目标板控制器 (EN/BOOT 输出) — 需要传引脚号
    target_ctrl = TargetController(config.PIN_TARGET_EN, config.PIN_TARGET_BOOT)

    # 4. UART1 与目标板通信
    #    MicroPython ESP32 UART 构造: UART(id, baudrate, tx=, rx=, timeout=)
    uart = UART(config.TARGET_UART_NUM,
                config.INITIAL_BAUDRATE,
                tx=Pin(config.PIN_UART_TX),
                rx=Pin(config.PIN_UART_RX),
                timeout=int(config.COMMAND_TIMEOUT * 1000),
                read_buf_len=config.READ_BUFFER_LEN)

    if config.LOG_LEVEL >= 2:
        print("[INFO] hardware initialized")
        print("[INFO]   UART%d @ %d bps, TX=GPIO%d RX=GPIO%d"
              % (config.TARGET_UART_NUM, config.INITIAL_BAUDRATE,
                 config.PIN_UART_TX, config.PIN_UART_RX))
        print("[INFO]   EN=GPIO%d BOOT=GPIO%d"
              % (config.PIN_TARGET_EN, config.PIN_TARGET_BOOT))
        print("[INFO]   firmware slots: %s" % config.FIRMWARE_SELECT_PINS)


def wait_for_target_and_firmware():
    """等待条件满足: 目标板已连接 + 一个固件被选中.

    持续轮询 GPIO 和串口, LED 反映当前等待状态.
    返回 (sel_pin, firmware_path) 元组.
    """
    led.set_state("searching")

    while True:
        # 1. 检查固件选择
        try:
            sel_pin, fw_filename = selector.read_selection()
        except ValueError as e:
            if config.LOG_LEVEL >= 1:
                print("[ERR] %s" % e)
            led.set_state("error")
            utime.sleep(2)
            led.set_state("searching")
            continue

        if fw_filename is None:
            # 没选中任何固件 (或选中了空槽位)
            if sel_pin is not None:
                if config.LOG_LEVEL >= 1:
                    print("[WARN] GPIO %d selected but no firmware mapped" % sel_pin)
            else:
                if config.LOG_LEVEL >= 3:
                    print("[DBG] no firmware selected, waiting...")
            led.set_state("no_firmware_sel")
            # 短暂 tick 一下 LED
            _tick_loop(led, config.POLL_INTERVAL_SEC)
            continue

        # 2. 固件文件存在性检查
        try:
            fw_path = selector.find_firmware_path(fw_filename)
        except FileNotFoundError as e:
            if config.LOG_LEVEL >= 1:
                print("[ERR] %s" % e)
                print("[ERR] available files:")
                for f in selector.list_available_firmwares():
                    print("       %s" % f)
            led.set_state("error")
            _tick_loop(led, 2.0)
            continue

        # 3. 检测目标板 (复用 ESPFlash.bootloader 探测)
        if config.LOG_LEVEL >= 2:
            print("[INFO] firmware selected: %s (GPIO %d)" % (fw_filename, sel_pin))
        # 创建临时 StubFlasher 用于探测 (内部调 bootloader 即 SYNC)
        probe_tool = StubFlasher(target_ctrl.reset_pin, target_ctrl.gpio0_pin, uart,
                                  log_enabled=(config.LOG_LEVEL >= 3))
        if target_ctrl.detect_target(probe_tool):
            return sel_pin, fw_path

        # 4. 目标板未连接, 等待重试
        if config.LOG_LEVEL >= 2:
            print("[INFO] target not detected, retry in %ds..."
                  % config.POLL_INTERVAL_SEC)
        led.set_state("searching")
        _tick_loop(led, config.POLL_INTERVAL_SEC)


def _tick_loop(led_obj, duration_sec):
    """在 duration_sec 秒内反复调用 led.tick(), 让 LED 闪烁.

    用小块 sleep 而非单个 sleep, 以保证 LED 闪烁频率准确.
    """
    end = utime.ticks_add(utime.ticks_ms(), int(duration_sec * 1000))
    while utime.ticks_diff(end, utime.ticks_ms()) > 0:
        led_obj.tick()
        utime.sleep_ms(20)


def do_flash(fw_path):
    """执行一次完整的烧录流程.

    返回 True 表示成功, False 表示失败.

    架构 (复用 micropython-lib 的 ESPFlash + 扩展):
      - 进入下载模式 + SYNC + flash_begin/data/end + MD5 + reboot
        → 复用 ESPFlash API (bootloader / flash_write_file / flash_verify_file / reboot)
      - stub loader + 芯片检测 → 我们扩展 (StubFlasher 类)
      - 错误码体系 → 我们扩展 (15 个错误类)
      - 烧后版本验证 → 我们扩展 (TargetVerifier, 借鉴 Machiel80)
      - 烧后日志监控 → 我们扩展 (TargetMonitor, 借鉴 helghast098)

    流程阶段:
      1. entering_boot: 进入下载模式 + SYNC (复用 ESPFlash.bootloader)
      2. (可选) detecting_chip: 自动识别芯片型号 (扩展)
      3. (可选) loading_stub: 加载对应 stub binary (扩展)
      4. set_baudrate: 切换到高速 (复用 ESPFlash.set_baudrate)
      5. flash_attach + flash_config: 配置 flash (复用 ESPFlash)
      6. flashing: 写入固件 (复用 ESPFlash.flash_write_file)
      7. verifying: MD5 校验 (复用 ESPFlash.flash_verify_file)
      8. success: 烧录成功, 复位目标板 (复用 ESPFlash.reboot)
      9. (可选) post_verifying: 烧后版本验证 (扩展, 借鉴 Machiel80)
      10. (可选) monitoring: 烧后日志监控 (扩展, 借鉴 helghast098)
    """
    # 创建 StubFlasher (继承自 ESPFlash, 复用所有底层方法)
    tool = StubFlasher(target_ctrl.reset_pin, target_ctrl.gpio0_pin, uart,
                       log_enabled=(config.LOG_LEVEL >= 3))

    # ---- 阶段 1: 进入下载模式 + SYNC (复用 ESPFlash.bootloader) ----
    led.set_state("entering_boot")
    try:
        tool.bootloader()   # ESPFlash 内部做了 EN/BOOT 时序 + SYNC 重试
    except Exception as e:
        print("[ERR] enter download mode / SYNC failed: %s" % e)
        return False

    # ---- 阶段 1.5: 切换工作波特率 (复用 ESPFlash.set_baudrate) ----
    if config.WORK_BAUDRATE != config.INITIAL_BAUDRATE:
        try:
            tool.set_baudrate(config.WORK_BAUDRATE)
        except Exception as e:
            print("[WARN] change baudrate failed: %s, staying at %d"
                  % (e, config.INITIAL_BAUDRATE))

    # ---- 阶段 2: 芯片型号检测 (扩展) ----
    if config.STUB_LOADER_ENABLE:
        led.set_state("detecting_chip")
        detected_chip_key = None
        detected_chip_name = "Unknown"
        try:
            if config.LOG_LEVEL >= 1:
                print("[INFO] detecting target chip for stub selection...")
            detected_chip_key, detected_chip_name = tool.detect_chip()
            if config.LOG_LEVEL >= 1:
                print("[INFO] detected: %s" % detected_chip_name)
        except SyncFailed as e:
            print("[WARN] chip detection failed: %s" % e)
            print("[WARN] will try stub loader with default (esp32) file")
            detected_chip_key = "esp32"
            detected_chip_name = "ESP32 (fallback)"

        # ---- 阶段 3: Stub Loader 加载 (扩展) ----
        led.set_state("loading_stub")
        try:
            if config.LOG_LEVEL >= 1:
                print("[INFO] loading stub loader for %s..." % detected_chip_name)
            tool.load_stub(chip_key=detected_chip_key)
            if config.LOG_LEVEL >= 1:
                print("[INFO] stub loader active, switching to %d bps..."
                      % config.STUB_WORK_BAUDRATE)
            tool.set_baudrate(config.STUB_WORK_BAUDRATE)
        except FileNotFoundError as e:
            print("[WARN] stub loader not available for %s: %s"
                  % (detected_chip_name, e))
            print("[WARN] falling back to ROM direct (slower)")
        except (SyncFailed, ESPROMError) as e:
            print("[WARN] stub loader failed for %s: %s" % (detected_chip_name, e))
            print("[WARN] falling back to ROM direct (slower)")
            try:
                tool.bootloader()  # 重新进入下载模式 + SYNC
            except Exception:
                print("[ERR] re-sync after stub failure also failed, aborting")
                return False

    # ---- 阶段 5: flash_attach + flash_config (复用 ESPFlash) ----
    try:
        tool.flash_attach()
        # 读 flash 大小并配置 (ESPFlash 内部处理)
        flash_size = tool.flash_read_size()
        tool.flash_config(flash_size)
    except Exception as e:
        print("[ERR] flash attach/config failed: %s" % e)
        return False

    # ---- 阶段 6-7: 烧录 + MD5 校验 (复用 ESPFlash + 扩展错误处理) ----
    led.set_state("flashing")
    try:
        tool.flash_file(fw_path, offset=config.FLASH_WRITE_OFFSET, verify=True)
    except FileNotFoundError as e:
        print("[ERR] firmware file not found: %s" % e)
        return False
    except EmptyFirmwareFile as e:
        print("[ERR] firmware file is empty: %s" % e)
        return False
    except FlashMd5Mismatch as e:
        print("[ERR] MD5 mismatch (data transfer error): %s" % e)
        return False
    except FlashWriteFailed as e:
        print("[ERR] flash write failed: %s" % e)
        return False
    except ESPROMError as e:
        print("[ERR] protocol error [%s]: %s" % (e.ERR_CODE, e))
        return False
    except Exception as e:
        # ESPFlash 内部抛通用 Exception, 转为 FlashWriteFailed
        print("[ERR] flash failed: %s" % e)
        return False

    # ---- 阶段 8: 烧录成功, 复位目标板 (复用 ESPFlash.reboot) ----
    led.set_state("success")
    if config.LOG_LEVEL >= 1:
        print("[INFO] === FLASH SUCCESS ===")
        print("[INFO] firmware: %s" % fw_path.split('/')[-1])
    utime.sleep(1.0)

    try:
        tool.reboot()
    except Exception as e:
        if config.LOG_LEVEL >= 2:
            print("[DBG] reboot raised: %s (may be normal)" % e)

    if config.AUTO_RESET_AFTER_FLASH:
        utime.sleep(config.RESET_DELAY_AFTER_FLASH)
        target_ctrl.reset_target()
        if config.LOG_LEVEL >= 2:
            print("[INFO] target reset, new firmware should be running")

    # ---- 阶段 9: 烧后版本验证 (扩展, 借鉴 Machiel80 FlashBox) ----
    if config.POST_FLASH_VERIFY_ENABLE:
        led.set_state("post_verifying")
        verifier = TargetVerifier(uart)
        try:
            verify_ok, verify_msg = verifier.verify_after_flash(target_ctrl)
        except Exception as e:
            print("[WARN] post-flash verify raised exception: %s" % e)
            verify_ok = False
            verify_msg = str(e)

        if verify_ok:
            if config.LOG_LEVEL >= 1:
                print("[INFO] post-flash verify OK: %s" % verify_msg)
        else:
            print("[WARN] post-flash verify FAILED: %s" % verify_msg)

    # ---- 阶段 10: 烧后日志监控 (扩展, 借鉴 helghast098 ESP32_flasher) ----
    if config.POST_FLASH_MONITOR_ENABLE:
        led.set_state("monitoring")
        monitor = TargetMonitor(uart)
        try:
            exit_reason = monitor.start_monitoring(led)
            if config.LOG_LEVEL >= 2:
                print("[INFO] monitoring exited: %s" % exit_reason)
        except Exception as e:
            print("[WARN] monitor raised exception: %s" % e)

    return True


def main():
    """主循环: 持续检测目标板 + 选中固件 → 自动烧录 → 重复."""
    init_hardware()

    if config.LOG_LEVEL >= 1:
        print("[INFO] %s v%s starting up..." % (config.APP_NAME, config.APP_VERSION))
        print("[INFO] continuous mode: %s" % config.CONTINUOUS_MODE)

    utime.sleep(config.STARTUP_DELAY_SEC)

    # 主循环
    while True:
        try:
            # 1. 等待条件满足 (固件选中 + 目标板连接)
            sel_pin, fw_path = wait_for_target_and_firmware()
        except Exception as e:
            # 这是不该发生的兜底异常
            if config.LOG_LEVEL >= 1:
                print("[ERR] unexpected: %s" % e)
            led.set_state("error")
            utime.sleep(2)
            continue

        # 2. 执行烧录 (含失败重试)
        success = False
        for attempt in range(1, config.MAX_RETRY_COUNT + 1):
            if config.LOG_LEVEL >= 1:
                print("[INFO] === attempt %d/%d ===" % (attempt, config.MAX_RETRY_COUNT))
            success = do_flash(fw_path)
            if success:
                break
            # 失败重试
            if attempt < config.MAX_RETRY_COUNT:
                if config.LOG_LEVEL >= 1:
                    print("[WARN] failed, retry in %ds..."
                          % config.RETRY_DELAY_SEC)
                led.set_state("error")
                _tick_loop(led, config.RETRY_DELAY_SEC)
            else:
                if config.LOG_LEVEL >= 1:
                    print("[ERR] all %d attempts failed"
                          % config.MAX_RETRY_COUNT)
                led.set_state("error")

        # 3. 决定下一步
        if not config.CONTINUOUS_MODE:
            if success:
                if config.LOG_LEVEL >= 1:
                    print("[INFO] done, exiting continuous loop")
                # 永远亮 LED 表示完成
                led.set_state("success")
                while True:
                    utime.sleep(1)
            else:
                # 失败后也停在这里
                while True:
                    utime.sleep(1)

        # 持续模式: 等待一会儿, 进入下一轮检测
        if success:
            if config.LOG_LEVEL >= 2:
                print("[INFO] next scan in %ds..."
                      % config.RESCAN_DELAY_AFTER_FLASH)
            _tick_loop(led, config.RESCAN_DELAY_AFTER_FLASH)


# ============================================================================
# 程序入口
# ============================================================================
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] interrupted by user")
        if led:
            led.off()
        sys.exit(0)
    except Exception as e:
        # 顶层异常捕获, 避免程序崩溃后 LED 状态卡住
        print("[FATAL] %s: %s" % (type(e).__name__, e))
        import sys
        sys.print_exception(e)
        if led:
            led.set_state("error")
            # 死循环保持错误状态
            while True:
                led.tick()
                utime.sleep_ms(50)
