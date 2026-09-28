"""
led_state.py
===========
状态机驱动的 LED 闪烁控制器.

通过单一后台任务持续闪烁板载 LED, 不同状态对应不同频率, 直观反映程序进度:
  - 空闲:          0.5 Hz (2秒一周期)
  - 探测目标板:    2 Hz
  - 进入下载模式: 5 Hz
  - SYNC 握手:    8 Hz
  - stub 加载中:  7 Hz (借鉴 esptool.py: 上传 stub binary 到 RAM)
  - 擦除中:       0.2 Hz (呼吸)
  - 烧录中:       10 Hz (急速)
  - MD5 校验中:   4 Hz
  - 烧后验证中:   6 Hz (借鉴 Machiel80: 发 version 命令读回版本号)
  - 日志监控中:   1.5 Hz (借鉴 helghast098: 被动监听目标板日志)
  - 烧录成功:     常亮
  - 出错:         常灭
  - 无固件被选中: 0.1 Hz (慢报警)

用法:
    led = LedStatus(pin=config.PIN_STATUS_LED)
    led.set_state("idle")
    # ... 在主循环中调用 led.tick() 维持闪烁
    led.set_state("flashing")

注意: MicroPython 默认无 _thread 模块 (某些版本有). 故采用协作式 tick:
    主程序在每次循环或 utime.sleep 后调用 led.tick() 即可.
"""

import utime
from machine import Pin
import config


# 状态机定义: 状态名 → 频率 (Hz)
# 0.0 = 常亮, -1.0 = 常灭
STATES = {
    "idle":              config.LED_FREQ_IDLE,
    "searching":         config.LED_FREQ_TARGET_SEARCHING,
    "default_fallback":  config.LED_FREQ_DEFAULT_FALLBACK,
    "entering_boot":     config.LED_FREQ_ENTER_BOOTLOADER,
    "syncing":           config.LED_FREQ_SYNCING,
    # 芯片型号检测阶段 (借鉴 esptool.py 的 detect_chip)
    "detecting_chip":    config.LED_FREQ_DETECTING_CHIP,
    # stub loader 加载阶段 (借鉴 esptool.py 的 stub 加载)
    "loading_stub":      config.LED_FREQ_LOADING_STUB,
    "erasing":           config.LED_FREQ_ERASING,
    "flashing":          config.LED_FREQ_FLASHING,
    "verifying":         config.LED_FREQ_VERIFYING,
    # 新增: 借鉴 Machiel80 FlashBox 的烧后版本验证阶段
    "post_verifying":    config.LED_FREQ_POST_VERIFY,
    # 新增: 借鉴 helghast098 的烧后日志监控阶段
    "monitoring":        config.LED_FREQ_MONITORING,
    "success":           config.LED_FREQ_SUCCESS,
    "error":             config.LED_FREQ_ERROR,
    "no_firmware_sel":   config.LED_FREQ_NO_FIRMWARE_SEL,
}


class LedStatus:
    """协作式 LED 状态机.

    set_state(name) 切换状态, tick() 在主循环中调用以驱动闪烁.
    """

    def __init__(self, pin_num=None, active_low=None):
        """
        :param pin_num: LED 引脚号, 默认取 config.PIN_STATUS_LED
        :param active_low: True=低电平点亮, 默认取 config.LED_ACTIVE_LOW
        """
        if pin_num is None:
            pin_num = config.PIN_STATUS_LED
        if active_low is None:
            active_low = config.LED_ACTIVE_LOW
        self._active_low = active_low
        self._pin = Pin(pin_num, Pin.OUT)
        self._state = "idle"
        self._freq = STATES.get("idle", 0.5)
        self._last_toggle_ms = utime.ticks_ms()
        self._led_on = False  # 内部逻辑状态 (不区分 active_low)

        # 初始关闭 LED
        self._apply_output(False)

    def set_state(self, state_name):
        """切换 LED 状态. 未知状态名回退到 'idle'."""
        if state_name not in STATES:
            if config.LOG_LEVEL >= 1:
                print("[WARN] unknown LED state '%s', fallback to idle" % state_name)
            state_name = "idle"
        if state_name != self._state:
            if config.LOG_LEVEL >= 3:
                print("[DBG] LED state: %s -> %s" % (self._state, state_name))
            self._state = state_name
            self._freq = STATES[state_name]
            self._last_toggle_ms = utime.ticks_ms()
            # 状态切换时, 立即按新频率设置 LED
            self._apply_initial_state()

    def get_state(self):
        """获取当前状态名."""
        return self._state

    def tick(self):
        """在主循环中调用, 驱动 LED 闪烁.

        频率 > 0 时按频率翻转 LED.
        频率 == 0: 常亮.
        频率 == -1: 常灭.
        """
        if self._freq == 0.0:
            # 常亮, 已在 set_state 时设置
            return
        if self._freq == -1.0:
            # 常灭, 已在 set_state 时设置
            return

        # 闪烁: 周期 T = 1/f, 半周期 T/2 = 0.5/f
        period_half_ms = int(500.0 / self._freq)
        if period_half_ms <= 0:
            period_half_ms = 1
        now = utime.ticks_ms()
        if utime.ticks_diff(now, self._last_toggle_ms) >= period_half_ms:
            self._led_on = not self._led_on
            self._apply_output(self._led_on)
            self._last_toggle_ms = now

    def on(self):
        """手动点亮 (覆盖状态)."""
        self._apply_output(True)

    def off(self):
        """手动熄灭 (覆盖状态)."""
        self._apply_output(False)

    # ---------------------------------------
    # 内部辅助
    # ---------------------------------------
    def _apply_output(self, logical_on):
        """根据 active_low 把逻辑状态映射到物理电平."""
        if self._active_low:
            # 低电平点亮: logical_on=True → 输出 0
            self._pin.value(0 if logical_on else 1)
        else:
            # 高电平点亮: logical_on=True → 输出 1
            self._pin.value(1 if logical_on else 0)

    def _apply_initial_state(self):
        """状态切换时, 设置 LED 初始态."""
        if self._freq == 0.0:
            # 常亮
            self._led_on = True
            self._apply_output(True)
        elif self._freq == -1.0:
            # 常灭
            self._led_on = False
            self._apply_output(False)
        else:
            # 闪烁: 初始点亮, 等下次 tick 翻转
            self._led_on = True
            self._apply_output(True)
            self._last_toggle_ms = utime.ticks_ms()


def list_states():
    """列出所有可用的状态名 (调试用)."""
    return list(STATES.keys())
