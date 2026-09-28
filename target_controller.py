"""
target_controller.py
==================
目标板控制器: 包装 EN/BOOT 引脚控制 + 目标板存在性检测.

简化说明:
  - 原 enter_download_mode / reset_target 的时序逻辑已在 micropython-lib 的
    ESPFlash.bootloader() 中实现 (调用 reset_pin / gpio0_pin 函数)
  - 本模块只保留:
    1. 包装 Pin 为可调用对象 (ESPFlash 期望 reset_pin/gpio0_pin 是 callable)
    2. detect_target: 通过发 SYNC 探测目标板是否连接 (esptool 没有等价功能)
    3. reset_target: 单独复位 (运行模式, ESPFlash.bootloader 总是进入下载模式)

借鉴关系:
  - bootloader 时序 → micropython-lib/espflash.py 的 bootloader() 方法
  - detect_target → 自实现 (esptool 没有"探测目标板是否存在"的 API)
"""

import utime
from machine import Pin
import config


class TargetController:
    """目标板控制器.

    提供两个 callable (reset_pin, gpio0_pin) 供 ESPFlash 使用,
    以及独立的 detect_target / reset_target 方法.
    """

    def __init__(self, en_pin_num, boot_pin_num):
        """
        :param en_pin_num: EN (RESET) 引脚号
        :param boot_pin_num: GPIO0 (BOOT) 引脚号
        """
        self._en = Pin(en_pin_num, Pin.OUT, value=1)
        self._boot = Pin(boot_pin_num, Pin.OUT, value=1)
        if config.LOG_LEVEL >= 3:
            print("[DBG] target controller ready: EN=GPIO%d BOOT=GPIO%d"
                  % (en_pin_num, boot_pin_num))

    # ---- 作为 callable 供 ESPFlash 使用 ----
    # ESPFlash.bootloader() 会调 reset_pin(0/1) 和 gpio0_pin(0/1)
    def reset_pin(self, value):
        """ESPFlash 期望的 reset callable."""
        self._en.value(value)

    def gpio0_pin(self, value):
        """ESPFlash 期望的 gpio0 callable."""
        self._boot.value(value)

    # ---- 独立方法 ----
    def enter_download_mode(self):
        """让目标板进入 ROM 下载模式.

        注意: 推荐直接用 ESPFlash.bootloader() 替代, 它实现了更完善的重试逻辑.
        本方法保留是为了与旧 main.py 兼容.
        """
        self._boot.value(0)   # BOOT=LOW
        utime.sleep_ms(50)
        self._en.value(0)     # EN 复位脉冲
        utime.sleep_ms(100)
        self._en.value(1)
        utime.sleep_ms(50)

    def reset_target(self):
        """复位目标板 (正常运行模式, GPIO0=HIGH)."""
        self._boot.value(1)
        utime.sleep_ms(10)
        self._en.value(0)
        utime.sleep_ms(100)
        self._en.value(1)
        utime.sleep_ms(200)

    def detect_target(self, esp_flash):
        """检测目标板是否存在.

        通过尝试 SYNC 握手判断目标板是否连接.
        借鉴: esptool 没有"探测"API, 这是本项目特有功能.

        :param esp_flash: ESPFlash 或 StubFlasher 实例
        :return: True 表示目标板已连接且 SYNC 成功
        """
        if config.LOG_LEVEL >= 2:
            print("[INFO] detecting target board...")
        try:
            # ESPFlash.bootloader 会进入下载模式 + SYNC, 失败抛异常
            esp_flash.bootloader()
            if config.LOG_LEVEL >= 2:
                print("[INFO] target detected (SYNC OK)")
            return True
        except Exception as e:
            if config.LOG_LEVEL >= 2:
                print("[INFO] no target detected: %s" % e)
            return False
