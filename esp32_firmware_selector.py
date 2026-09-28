"""
firmware_selector.py
===================
通过 GPIO 通断选择要烧录的固件.

工作原理:
  - 每个 FIRMWARE_SELECT_PIN 配置为 INPUT + 内部下拉 (PULL_DOWN)
  - 默认为低电平 (0) → 未选中
  - 跳线到 3V3 → 高电平 (1) → 选中该槽位对应固件
  - 同时只允许一个槽位有效; 若多个同时有效, 报错

文件名映射在 config.FIRMWARE_MAP 中:
  {13: "ESP32_GENERIC-20260824-v1.29.0.bin",
   14: "ESP32_GENERIC-20260406-v1.28.0.bin",
   27: "ESP32_GENERIC-20251209-v1.27.0.bin",
   26: None}    # 槽位 3 保留

用法:
    sel = FirmwareSelector()
    sel_pin, fw_filename = sel.read_selection()
    if fw_filename is None:
        print("没有有效固件选中")
    else:
        print("GPIO %d 选中 -> %s" % (sel_pin, fw_filename))
"""

from machine import Pin
import utime
import config
import os


class FirmwareSelector:
    """从 GPIO 状态读取固件选择."""

    def __init__(self):
        # 为每个 GPIO 创建输入 Pin, 启用内部下拉
        self._pins = {}
        for pin_num in config.FIRMWARE_SELECT_PINS:
            self._pins[pin_num] = Pin(pin_num, Pin.IN, Pin.PULL_DOWN)
        if config.LOG_LEVEL >= 2:
            print("[INFO] firmware selector ready, watching GPIO: %s"
                  % config.FIRMWARE_SELECT_PINS)

    def read_selection(self):
        """读取当前选中槽位.

        :return: (sel_pin, firmware_filename)
                 若无任何选中: (None, None)
                 若多个同时选中: 抛 ValueError
        """
        active = []
        for pin_num, p in self._pins.items():
            # 读两次去抖: 间隔 DEBOUNCE_MS
            v1 = p.value()
            utime.sleep_ms(config.DEBOUNCE_MS)
            v2 = p.value()
            if v1 and v2:
                active.append(pin_num)

        if len(active) == 0:
            return None, None
        if len(active) > 1:
            raise ValueError(
                "Multiple firmware select pins active: %s (only one allowed)"
                % active)

        sel_pin = active[0]
        # 从 FIRMWARE_MAP 查文件名
        fw_name = config.FIRMWARE_MAP.get(sel_pin)
        return sel_pin, fw_name

    def find_firmware_path(self, fw_filename):
        """将固件文件名转为完整路径, 并检查文件是否存在.

        :return: 完整路径 (str)
        :raises FileNotFoundError: 文件不存在
        """
        full_path = config.FIRMWARE_DIR.rstrip('/') + '/' + fw_filename
        try:
            os.stat(full_path)
            return full_path
        except OSError:
            raise FileNotFoundError(
                "firmware file not found: %s (in %s/)"
                % (fw_filename, config.FIRMWARE_DIR))

    def list_available_firmwares(self):
        """列出 /firmware/ 目录下所有 .bin 文件 (调试用)."""
        try:
            files = os.listdir(config.FIRMWARE_DIR)
        except OSError as e:
            print("[ERR] cannot list %s: %s" % (config.FIRMWARE_DIR, e))
            return []
        bins = [f for f in files if f.endswith('.bin')]
        return sorted(bins)
