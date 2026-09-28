"""
config.py
========
ESP32 固件烧录器 —— 全部可配置参数集中于此文件。

修改本文件即可适配你的硬件接线、固件清单、行为开关，无需改动其他源码。

作者 : Z.ai
日期 : 2026-09-28
许可 : MIT
"""

# ============================================================================
# 1. 硬件引脚配置 (Hardware Pin Configuration)
# ============================================================================
# 说明:
#   - GPIO 6/7/8/9/10/11 用于片内 SPI Flash, 严禁使用
#   - GPIO0 是 strapping pin (启动时必须为高, 否则进入下载模式), 作为输入可用
#   - GPIO1/3 是 UART0 默认脚 (REPL 调试输出), 默认保留不占用
#   - GPIO2 在大多数 ESP32 开发板上接板载 LED
#   - GPIO12 (MTDI) 启动时必须为低, 否则 Flash 电压被设为 1.8V
#   - GPIO15 启动时必须为低, 否则 boot messages 不打印
#   => 故选 GPIO4/5 作 BOOT/EN 控制, GPIO13/14/27/26 作固件选择输入
# ----------------------------------------------------------------------------

# --- 目标板控制引脚 (OUTPUT, 主机 → 目标板) ---
# 用于自动让目标板进入下载模式 (Download Boot Mode)
# 接线: 主机 GPIO5  → 目标板 EN (RESET), 串联 100Ω 防止浪涌
#       主机 GPIO4  → 目标板 GPIO0 (BOOT), 串联 100Ω
PIN_TARGET_EN    = 5      # 主机 GPIO5  → 目标板 EN
PIN_TARGET_BOOT  = 4      # 主机 GPIO4  → 目标板 GPIO0

# --- 目标板通信 UART ---
# 主机用 UART1 与目标板通信 (UART0 留作 REPL 调试)
# UART1 默认脚是 GPIO4/5 (TX/RX), 与上面 BOOT/EN 控制冲突,
# 因此 remap 到 GPIO18/19 (这两个引脚无 strapping 限制, 输入输出均可)
# 接线: 主机 GPIO18 (TX) → 目标板 GPIO3 (UART0 RX)
#       主机 GPIO19 (RX) → 目标板 GPIO1 (UART0 TX)
TARGET_UART_NUM  = 1      # UART 编号 (1 或 2)
PIN_UART_TX      = 18     # 主机 TX 脚
PIN_UART_RX      = 19     # 主机 RX 脚

# --- 固件选择 GPIO (INPUT, 默认下拉, 跳线到 3V3 即选中) ---
# 注意: 用户最初要求 GPIO1/2/3/4, 但 GPIO1/3 与 UART0 冲突 (会丢失 REPL),
#       GPIO4 已被分配为 BOOT 控制, 故替换为 GPIO13/14/27/26.
# 若坚持使用其他引脚, 可在此处自由修改.
# 接线: 每个引脚经一个 10k 下拉电阻到 GND, 按钮或跳线到 3V3
FIRMWARE_SELECT_PINS = [
    13,   # 槽位 0 → 第一个固件 (v1.29.0)
    14,   # 槽位 1 → 第二个固件 (v1.28.0)
    27,   # 槽位 2 → 第三个固件 (v1.27.0)
    26,   # 槽位 3 → 第四个固件 (保留, 默认无固件)
]


# ============================================================================
# 2. 状态指示 LED (Status LED)
# ============================================================================
# 大多数 ESP32 开发板板载 LED 在 GPIO2 (低电平点亮). 部分板子 (如 ESP32-CAM)
# 在 GPIO4, 部分板子没有板载 LED 需外接. 自行按板子型号修改.
PIN_STATUS_LED   = 2      # 状态 LED 引脚
LED_ACTIVE_LOW   = False  # True: 低电平点亮 (如部分老 NodeMCU); False: 高电平点亮

# LED 闪烁频率 (Hz) —— 对应不同状态
# 频率越高闪烁越快, 0 = 常亮, -1 = 常灭
LED_FREQ_IDLE             = 0.5   # 空闲: 慢闪 (0.5Hz, 2秒一周期)
LED_FREQ_TARGET_SEARCHING = 2.0   # 正在探测目标板: 中速闪 (2Hz)
LED_FREQ_ENTER_BOOTLOADER = 5.0   # 进入下载模式中: 快闪 (5Hz)
LED_FREQ_SYNCING          = 8.0   # SYNC 握手中: 极快闪 (8Hz)
# 芯片型号检测中 (借鉴 esptool.py 的 detect_chip)
LED_FREQ_DETECTING_CHIP   = 6.0   # 检测中: 6Hz (与 syncing 区分)
LED_FREQ_ERASING          = 0.2   # 擦除中: 慢呼吸 (0.2Hz)
LED_FREQ_FLASHING         = 10.0  # 烧录中: 急速闪 (10Hz, 表示数据传输)
LED_FREQ_VERIFYING        = 4.0   # MD5 校验中: 中快闪 (4Hz)
# stub loader 加载中 (借鉴 esptool.py 的 stub 加载阶段)
# 上传 ~3.6KB stub binary 到目标板 RAM, 通常耗时 <1 秒
LED_FREQ_LOADING_STUB     = 7.0   # stub 加载中: 7Hz 急速闪 (区别于 flashing 的 10Hz)
# 借鉴 Machiel80: 烧后验证阶段 (发 version 命令读回版本号)
LED_FREQ_POST_VERIFY      = 6.0   # 烧后验证中: 较快闪 (6Hz, 与烧录中区别)
# 借鉴 helghast098: 烧后日志监控阶段
LED_FREQ_MONITORING        = 1.5   # 日志监控中: 慢闪 (1.5Hz, 表示在被动监听)
LED_FREQ_SUCCESS          = 0.0   # 烧录成功: 常亮 (0 = steady on)
LED_FREQ_ERROR            = -1.0  # 出错: 常灭 (红灯? 用外部 LED)
LED_FREQ_NO_FIRMWARE_SEL  = 0.1   # 没有选中任何固件: 极慢闪 (报警)


# ============================================================================
# 3. 串口烧录参数 (Serial Flashing Parameters)
# ============================================================================
# ESP32 ROM bootloader 默认 115200 bps, SYNC 后可切换到更高 (最高 921600).
# 但 ESP32 内置 ROM 在 >460800 时不稳定, 故保守用 115200.
INITIAL_BAUDRATE  = 115200   # 进入下载模式时的初始波特率
WORK_BAUDRATE     = 115200   # SYNC 后切换的工作波特率 (= INITIAL 表示不切换)
SYNC_RETRY_COUNT  = 7        # SYNC 重试次数 (esptool 默认 7)
SYNC_RETRY_DELAY  = 0.1       # 每次 SYNC 重试间隔 (秒)
COMMAND_TIMEOUT   = 3.0       # 普通命令超时 (秒)
ERASE_TIMEOUT     = 30.0      # 全片擦除超时 (秒, 大 flash 可能 20s+)
FLASH_BEGIN_TIMEOUT = 10.0    # FLASH_BEGIN 命令超时 (秒)
FLASH_BLOCK_TIMEOUT = 5.0     # 每个 FLASH_DATA 块超时 (秒)
MD5_TIMEOUT       = 10.0      # MD5 计算超时 (秒)
READ_BUFFER_LEN   = 2048      # UART 读缓冲长度

# 烧录前是否擦除整片 Flash (True=最干净, False=只擦要写的区域)
ERASE_FULL_FLASH_BEFORE_WRITE = True


# ============================================================================
# 4. Flash 写入参数 (Flash Write Block Sizing)
# ============================================================================
# ESP32 ROM 支持 0x1000 (4KB) ~ 0x4000 (16KB) 块大小.
# 块越大 → 通信开销越少 → 烧录越快, 但 RAM 占用更多.
# MicroPython 内存有限, 4KB 是稳妥选择.
FLASH_BLOCK_SIZE    = 0x1000         # 4 KB per block
FLASH_WRITE_OFFSET  = 0x0            # 写入 Flash 的起始地址 (ESP32_GENERIC 固件从 0 开始)
FIRMWARE_DIR        = "/firmware"     # 固件文件所在目录 (相对根文件系统)

# ---- Stub Loader 配置 (借鉴 esptool.py / espressif 官方库) ----
# stub loader 是 esptool 加速烧录的关键技术:
#   1. 先通过 ROM 协议 (MEM_BEGIN/MEM_DATA/MEM_END) 把 stub binary 写到目标板 RAM
#   2. 让目标板跳转到 stub entry 执行, stub 接管 UART
#   3. stub 用更快的 SPI 时序烧录 (40MHz+ vs ROM 的 10-20MHz), 实测 5-10x 加速
#   4. stub 还支持 read_flash、压缩传输等额外命令
#
# 启用后烧录流程多一个阶段 (loading_stub), 之后用 stub 协议继续烧录
STUB_LOADER_ENABLE       = True        # 是否启用 stub loader (False = 用 ROM 直跑, 慢但兼容性最好)
STUB_LOADER_DIR          = "/stub"     # stub binary 目录 (含多个芯片的 stub JSON)
ESP_RAM_BLOCK_SIZE       = 0x1800      # RAM 上传块大小 (6 KB, esptool 默认值)
STUB_OHAI_TIMEOUT        = 3.0         # 等 stub 发 OHAI 信号的超时 (秒)
# stub 加载后切换的工作波特率 (stub 支持更高, 因为 stub 用 PLL 时钟不受晶体频率影响)
# 默认 460800 (4x 加速), 可调到 921600 (8x 但线材要好)
STUB_WORK_BAUDRATE       = 460800

# ---- 芯片型号自动识别 (借鉴 esptool.py 的 detect_chip) ----
# 不同型号 ESP32 的 stub binary 完全不同 (架构/RAM 地址/SPI 控制器都不同):
#   - ESP32/S2/S3 是 Xtensa 架构, RAM 在 0x400xxxxx (IRAM)
#   - ESP32-C/H/P 系列是 RISC-V 架构, RAM 在 0x408xxxxx
#   - ESP8266 是单独的 LX106 架构
#
# 识别方法 (借鉴 esptool.py 的 detect_chip()):
#   - 老芯片 (ESP32/ESP8266/ESP32-S2): 读 magic value (寄存器 0x40001000) 比对
#   - 新芯片 (S3/C2/C3/C5/C6/H2/P4 等): 发 GET_SECURITY_INFO 命令读 chip_id
#
# 识别成功后, 自动从 STUB_LOADER_DIR 加载对应的 stub JSON 文件
CHIP_DETECT_MAGIC_REG    = 0x40001000  # magic value 寄存器地址 (老芯片用)
CHIP_DETECT_TIMEOUT      = 2.0          # 芯片识别超时 (秒)

# Magic value 到芯片型号的映射 (借鉴 esptool.py 的 ROM_LIST)
# 用于 ESP32/ESP8266/ESP32-S2 等老芯片
CHIP_MAGIC_VALUES = {
    0x00F01D83: ("esp32",       "ESP32"),       # 原版 ESP32 (Xtensa dual-core)
    0xFFF0C101: ("esp8266",     "ESP8266"),     # ESP8266 (LX106)
    0x000007C6: ("esp32s2",     "ESP32-S2"),    # ESP32-S2 (Xtensa single-core)
}

# IMAGE_CHIP_ID 到芯片型号的映射 (借鉴 esptool.py 的 IMAGE_CHIP_ID)
# 用于 ESP32-S3 及以后的新芯片 (走 GET_SECURITY_INFO 命令)
CHIP_ID_VALUES = {
    9:  ("esp32s3",     "ESP32-S3"),      # ESP32-S3
    12: ("esp32c2",     "ESP32-C2"),      # ESP32-C2
    5:  ("esp32c3",     "ESP32-C3"),      # ESP32-C3
    23: ("esp32c5",     "ESP32-C5"),      # ESP32-C5
    13: ("esp32c6",     "ESP32-C6"),      # ESP32-C6
    16: ("esp32h2",     "ESP32-H2"),      # ESP32-H2
    18: ("esp32p4",     "ESP32-P4"),      # ESP32-P4 (无 stub, 用 ROM 直跑)
}

# 芯片型号 → stub JSON 文件名映射 (实际加载用)
# 注意: 文件名必须与 STUB_LOADER_DIR 下的实际文件一致
CHIP_STUB_FILES = {
    "esp32":         "esp32_stub.json",
    # "esp32s2":       "esp32s2_stub.json",   # 4MB 精简: 已移除
    # "esp32s3":       "esp32s3_stub.json",   # 4MB 精简: 已移除
    # "esp32c2":       "esp32c2_stub.json",   # 4MB 精简: 已移除
    # "esp32c3":       "esp32c3_stub.json",   # 4MB 精简: 已移除
    # "esp32c5":       "esp32c5_stub.json",   # 4MB 精简: 已移除
    # "esp32c6":       "esp32c6_stub.json",   # 4MB 精简: 已移除
    # "esp32h2":       "esp32h2_stub.json",   # 4MB 精简: 已移除
    # "esp8266":       "esp8266_stub.json",  # 4MB 精简: 已移除
    # ESP32-P4 暂无 stub (用 ROM 直跑)
    # ESP32-C61/H21/H4/S31 暂无 stub (用 ROM 直跑)
}


# ============================================================================
# 5. 固件清单 (Firmware Inventory)
# ============================================================================
# 每个 (选择 GPIO, 文件名) 一对: 当对应 GPIO 被跳线到 3V3 时刷入该固件.
# 文件名需与 /firmware/ 目录下的实际文件名一致.
# 留 None 表示该槽位保留 (跳线后报 "no firmware" 错误).
FIRMWARE_MAP = {
    13: "ESP32_GENERIC-20260824-v1.29.0.bin",   # 槽位 0: 最新稳定版
    14: None,                                    # 槽位 1: 保留扩展
    26: None,                                    # 槽位 3: 保留扩展
}


# ============================================================================
# 6. 主程序行为 (Application Behavior)
# ============================================================================
# 启动后等待多久再开始检测目标板 (给目标板供电稳定时间)
STARTUP_DELAY_SEC        = 1.0

# 检测目标板存在时, 等多久再次确认 (避免误判, 单位毫秒)
DEBOUNCE_MS              = 50

# 目标板不存在时, 重新检测的间隔 (秒)
POLL_INTERVAL_SEC       = 1.0

# 烧录完成后是否自动复位目标板 (拉低 EN 让其重启运行新固件)
AUTO_RESET_AFTER_FLASH   = True

# 烧录完成后多久复位 (秒, 让 bootloader 完成收尾)
RESET_DELAY_AFTER_FLASH  = 0.5

# 烧录完成后多久开始新一轮检测 (秒, 给目标板启动时间)
RESCAN_DELAY_AFTER_FLASH = 5.0

# 烧录失败后是否自动重试
AUTO_RETRY_ON_FAILURE    = True
MAX_RETRY_COUNT          = 3
RETRY_DELAY_SEC          = 2.0

# 烧录完成后是否进入"已完成"循环 (一直循环检测+烧录, 直到断电或更换目标板)
CONTINUOUS_MODE          = True

# ============================================================================
# 6.1 烧后验证 (Post-Flash Verification)
# ============================================================================
# 借鉴 Machiel80 FlashBox: 烧完后让目标板复位运行新固件, 然后主机通过 UART
# 发送一个 "version\n" 命令, 期望目标板返回其版本号 (约定格式: "VERSION: x.y.z")
# 这样能确认:
#   1. 烧的固件确实跑起来了 (不是死在 bootloader)
#   2. 烧的固件版本与预期一致 (防止刷错文件)
#
# 启用后烧录流程多 2 个阶段: verifying (复位等启动) + verified (读回版本)
POST_FLASH_VERIFY_ENABLE         = True
# 复位后等多久才发 version 命令 (秒, 给目标板新固件启动时间)
POST_FLASH_BOOT_DELAY_SEC        = 3.0
# 发送 version 命令的字节 (可改为 "ver\n" / "info\n" 等)
POST_FLASH_VERSION_CMD           = b"version\n"
# 等待版本响应的超时 (秒)
POST_FLASH_VERSION_TIMEOUT_SEC   = 5.0
# 期望版本号前缀 (目标板返回 "VERSION: x.y.z" 时, 取 ": " 后部分对比)
# 留 None 表示不校验具体版本号, 只验证有响应即可
POST_FLASH_EXPECTED_VERSION_PREFIX = b"VERSION:"
# 期望的具体版本字符串 (留 None 表示只验证前缀, 不比对版本号本身)
# 例如: b"1.0.0" 表示目标板必须返回 "VERSION: 1.0.0" 才算验证通过
POST_FLASH_EXPECTED_VERSION      = None

# ============================================================================
# 6.2 烧后日志监控 (Post-Flash Logging)
# ============================================================================
# 借鉴 helghast098 ESP32_flasher: 烧完后起一个 task 持续读 UART 转发到 stdout,
# 便于实时看目标板新固件的日志输出 (REPL / printf 等)
#
# 注意: 监控期间主机会"卡住"在这个 task, 直到:
#   - 达到 POST_FLASH_MONITOR_DURATION 秒
#   - 用户拔掉目标板 (UART 收不到数据, 检测到目标板断开)
#   - 收到目标板发送的退出关键字 (POST_FLASH_MONITOR_EXIT_CMD)
POST_FLASH_MONITOR_ENABLE        = False  # 默认关闭 (持续模式不需要)
# 监控持续时间 (秒, 到点自动退出回到主循环)
POST_FLASH_MONITOR_DURATION_SEC  = 30.0
# 监控期间收到此关键字则提前退出 (例如目标板打印 "FLASH DONE" 后退出)
POST_FLASH_MONITOR_EXIT_CMD      = b"FLASH_DONE"


# ============================================================================
# 7. 调试输出 (Debug Logging)
# ============================================================================
# 控制运行时日志详细程度
#   0 = 完全静默
#   1 = ERROR + 主要状态变化
#   2 = + INFO (默认推荐)
#   3 = + DEBUG (协议细节, 适合排查)
LOG_LEVEL = 2

# 是否在 LOG_LEVEL >= 3 时打印每个 SLIP 包的 hex
LOG_SLIP_PACKETS = False


# ============================================================================
# 8. ESP32 ROM 协议常量 (一般无需修改)
# ============================================================================
# 这些是 ESP32 ROM bootloader 协议的标准常量, 仅在乐鑫变更协议时才需调整.
class ESPROM:
    """ESP32 ROM bootloader 协议常量 (只读)."""
    # SLIP 帧字符
    END     = 0xC0
    ESC     = 0xDB
    END_ESC = 0xDC
    ESC_ESC = 0xDD

    # 方向字节
    DIR_REQUEST  = 0x00
    DIR_RESPONSE = 0x01

    # 命令 ID
    CMD_FLASH_BEGIN     = 0x02
    CMD_FLASH_DATA      = 0x03
    CMD_FLASH_END       = 0x04
    CMD_MEM_BEGIN       = 0x05
    CMD_MEM_END         = 0x06
    CMD_MEM_DATA        = 0x07
    CMD_SYNC            = 0x08
    CMD_WRITE_REG       = 0x09
    CMD_READ_REG        = 0x0A
    CMD_SPI_SET_PARAMS  = 0x0B
    CMD_SPI_ATTACH      = 0x0D
    CMD_CHANGE_BAUDRATE = 0x0F
    CMD_FLASH_SIZE      = 0x0F
    CMD_ERASE_FLASH     = 0xD0
    CMD_ERASE_REGION    = 0xD1
    CMD_FLASH_MD5       = 0x13
    # 新增 (借鉴 esptool): 用于检测 ESP32-S3+ 芯片型号
    CMD_GET_SECURITY_INFO = 0x14    # 返回 chip_id, 用于 S3/C2/C3/C5/C6/H2/P4

    # 命令返回状态码
    STATUS_OK = 0

    # 目标板存在性检测参数
    SYNC_MAGIC = b'\x07\x07\x12\x20' + b'\x55' * 32   # SYNC 命令的 36 字节 magic

    # ESP32 SPI 寄存器 (用于 SPI attach)
    SPI_REG_BASE      = 0x3FF42000   # ESP32 SPI0/1 寄存器基址 (兼容旧版)
    SPI_USR_OFFS      = 0x1C
    SPI_USR1_OFFS     = 0x20
    SPI_CMD_OFFS      = 0x00
    SPI_W0_OFFS       = 0x80
    SPI_MS_DLEN_OFFS  = 0x2C


# ============================================================================
# 9. 应用元信息 (Application Metadata)
# ============================================================================
APP_NAME    = "ESP32 Firmware Flasher"
APP_VERSION = "1.0.0"
APP_AUTHOR  = "Z.ai"
APP_LICENSE = "MIT"

# 开机横幅
def print_banner():
    """开机打印横幅. 仅在 LOG_LEVEL >= 1 时输出."""
    banner = (
        "\n"
        "========================================\n"
        "  {name} v{ver}\n"
        "  {author} | {license}\n"
        "========================================\n"
    ).format(name=APP_NAME, ver=APP_VERSION, author=APP_AUTHOR, license=APP_LICENSE)
    print(banner)
