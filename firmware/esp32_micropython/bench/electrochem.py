from machine import Pin, SPI, SoftI2C
import time

# ================= 用户参数 =================

VREF = 2.5              # ADS1256参考电压，单位V
DAC_CODE = 621          # MCP4728输出约0.5V（3.3V供电）

# 检流电阻，单位Ω。实际为10kΩ时，改成10000.0
R_SHUNT = 10000.0

# 电极池常数，单位cm^-1；不知道则保留None
CELL_K_CM = 1.5

# 温度补偿暂不启用
MIN_SHUNT_V = 0.0001    # 100µV，低于此值不计算电导
MIN_CELL_V = 0.005      # 5mV，防止分母过小
MAX_CHANGE_V = 0.0005
MAX_CHANGE_RATIO = 0.05

# ================= 全局状态 =================

spi = None
cs = None
drdy = None
i2c = None
dac_address = None


# ================= MCP4728 =================

def dac_write(channel, code):
    if channel not in (0, 1, 2, 3):
        raise ValueError("DAC channel must be 0..3")
    if not isinstance(code, int) or not 0 <= code <= 4095:
        raise ValueError("DAC code must be 0..4095")

    # VREF=VDD，正常模式，立即更新，不写EEPROM
    i2c.writeto(
        dac_address,
        bytes([
            0x40 | (channel << 1),
            (code >> 8) & 0x0F,
            code & 0xFF
        ])
    )


# ================= ADS1256 =================

def wait_ready(timeout_ms=3000):
    start = time.ticks_ms()
    while drdy.value():
        if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
            raise RuntimeError("ADS1256 DRDY超时，请检查供电及GPIO16")
        time.sleep_ms(1)


def command(value):
    cs.value(0)
    try:
        spi.write(bytes([value]))
        time.sleep_us(10)
    finally:
        cs.value(1)
    time.sleep_us(10)


def read_register(address):
    wait_ready()
    cs.value(0)
    try:
        spi.write(bytes([0x10 | address, 0x00]))
        time.sleep_us(10)
        return spi.read(1)[0]
    finally:
        cs.value(1)


def write_register(address, value):
    wait_ready()
    cs.value(0)
    try:
        spi.write(bytes([0x50 | address, 0x00, value]))
        time.sleep_us(10)
    finally:
        cs.value(1)


def init_adc():
    command(0x00)       # WAKEUP
    time.sleep_ms(10)
    wait_ready()
    command(0x0F)       # SDATAC
    command(0xFE)       # RESET
    time.sleep_ms(10)
    wait_ready()

    status = read_register(0)
    chip_id = status >> 4
    print("ADS1256 STATUS=0x%02X，ID=%d" % (status, chip_id))

    if chip_id != 3:
        raise RuntimeError("ADS1256 ID错误，应为3")

    write_register(0, 0x02)  # 开启缓冲，MSB优先
    write_register(2, 0x00)  # 增益1，关闭检测电流源/时钟输出
    write_register(3, 0x63)  # 50SPS，晶振7.68MHz
    write_register(1, 0x01)  # A0-A1

    if (read_register(0) & 0x0E) != 0x02:
        raise RuntimeError("STATUS配置失败")
    if read_register(2) != 0x00:
        raise RuntimeError("ADCON配置失败")
    if read_register(3) != 0x63:
        raise RuntimeError("DRATE配置失败")

    command(0xF0)       # 自校准
    time.sleep_ms(5)
    wait_ready()
    print("ADS1256校准完成：缓冲开启，增益1，50SPS")


def read_voltage(positive, negative):
    write_register(1, (positive << 4) | negative)

    command(0xFC)       # SYNC
    command(0x00)       # WAKEUP
    time.sleep_ms(1)
    wait_ready()

    cs.value(0)
    try:
        spi.write(b"\x01")  # RDATA
        time.sleep_us(10)
        data = spi.read(3)
    finally:
        cs.value(1)

    raw = (data[0] << 16) | (data[1] << 8) | data[2]
    if raw & 0x800000:
        raw -= 1 << 24

    if raw >= 0x7FFFF0 or raw <= -0x7FFFF0:
        raise RuntimeError("ADC接近满量程，请检查输入")

    return raw * (2.0 * VREF) / 0x7FFFFF


# ================= 数据处理 =================

def sample_pair():
    # 前后重复采样，用于筛除部分快速变化。
    # 四次测量是顺序进行，不是同时采样。
    cell1 = read_voltage(0, 1)
    shunt1 = read_voltage(2, 3)
    cell2 = read_voltage(0, 1)
    shunt2 = read_voltage(2, 3)

    cell_limit = max(
        MAX_CHANGE_V,
        MAX_CHANGE_RATIO * max(abs(cell1), abs(cell2))
    )
    shunt_limit = max(
        MAX_CHANGE_V,
        MAX_CHANGE_RATIO * max(abs(shunt1), abs(shunt2))
    )

    changing = (
        abs(cell2 - cell1) > cell_limit
        or abs(shunt2 - shunt1) > shunt_limit
    )

    return (
        (cell1 + cell2) / 2,
        (shunt1 + shunt2) / 2,
        changing
    )


def show_result(cell_v, shunt_v, changing):
    print("电极实测电压：%+.6f V" % cell_v)
    print("电阻实测压降：%+.6f V" % shunt_v)

    if R_SHUNT is None:
        print("未填写R_SHUNT，暂不计算电流和电导率")
        return

    current_a = shunt_v / R_SHUNT
    print("电流：%+.4f uA" % (current_a * 1e6))

    if changing:
        print("状态：采样期间变化较快，跳过本组电导计算")
        return

    if cell_v < -MIN_CELL_V or shunt_v < -MIN_SHUNT_V:
        print("状态：出现反向电压，请检查接线或瞬态")
        return

    if abs(cell_v) < MIN_CELL_V:
        print("状态：电极电压过小，跳过计算")
        return

    if abs(shunt_v) < MIN_SHUNT_V:
        print("状态：电流过小/可能断路，电导率不可判定")
        return

    conductance_s = current_a / cell_v
    resistance_ohm = cell_v / current_a

    print("表观电阻：%.3f ohm" % resistance_ohm)
    print("表观电导：%.6f uS" % (conductance_s * 1e6))

    if CELL_K_CM is None:
        print("未填写CELL_K_CM，暂不换算电导率")
        return

    conductivity = conductance_s * CELL_K_CM
    print("表观电导率：%.6f uS/cm" % (conductivity * 1e6))
    print("            %.6f mS/cm" % (conductivity * 1e3))
    print("温度补偿：关闭")


# ================= 主程序 =================

def main():
    global spi, cs, drdy, i2c, dac_address

    if R_SHUNT is not None and R_SHUNT <= 0:
        raise ValueError("R_SHUNT必须大于0")
    if CELL_K_CM is not None and CELL_K_CM <= 0:
        raise ValueError("CELL_K_CM必须大于0")
    if not isinstance(DAC_CODE, int) or not 0 <= DAC_CODE <= 4095:
        raise ValueError("DAC_CODE必须为0～4095整数")

    # MCP4728：SDA=8，SCL=9
    i2c = SoftI2C(sda=Pin(8), scl=Pin(9), freq=100000)
    devices = i2c.scan()
    print("I2C设备：", [hex(a) for a in devices])

    candidates = [a for a in devices if 0x60 <= a <= 0x67]
    if len(candidates) != 1:
        raise RuntimeError("无法唯一识别MCP4728")

    dac_address = candidates[0]

    for channel in range(4):
        dac_write(channel, 0)

    # ADS1256：沿用你已经验证的引脚
    cs = Pin(5, Pin.OUT, value=1)
    drdy = Pin(16, Pin.IN, pull=Pin.PULL_UP)
    spi = SPI(
        2,
        baudrate=1_000_000,
        polarity=0,
        phase=1,
        sck=Pin(18),
        mosi=Pin(6),
        miso=Pin(19)
    )

    init_adc()
    dac_write(0, DAC_CODE)
    time.sleep_ms(300)

    print("\n开始实测，温度补偿关闭")
    print("当前为直流表观电导/电导率，含电极界面影响")
    print("按Ctrl+C停止，正常退出时尝试清零DAC A")

    start = time.ticks_ms()
    count = 0

    while True:
        cell_v, shunt_v, changing = sample_pair()
        count += 1
        elapsed = time.ticks_diff(time.ticks_ms(), start) / 1000

        print("\n--- 第%d组，%.2f秒 ---" % (count, elapsed))
        show_result(cell_v, shunt_v, changing)
        time.sleep_ms(500)


try:
    main()
except KeyboardInterrupt:
    print("\n已停止")
except Exception as error:
    print("\n错误：", error)
finally:
    # 即使初始化失败，也不会因变量未定义再次报错
    if cs is not None:
        try:
            cs.value(1)
        except Exception:
            pass

    if dac_address is not None and i2c is not None:
        try:
            dac_write(0, 0)
            print("DAC A已设置为0V")
        except Exception:
            print("DAC清零失败，请断开回路供电")