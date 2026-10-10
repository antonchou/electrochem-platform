from machine import Pin, SPI, SoftI2C
import time

# ---------- 用户设置 ----------
VREF = 2.5             # ADS1256 模块实际参考电压
R_SHUNT = None         # 检流电阻，单位 Ω；10kΩ 就改成 10000.0
DAC_CODE = 621         # A通道约0.5V，按MCP4728供电3.3V计算
                       # 此处是施加电压，不是测量结果

# ---------- MCP4728：已确认的接线 ----------
i2c = SoftI2C(sda=Pin(8), scl=Pin(9), freq=100000)
dac_address = None

def dac_write(channel, code):
    command = 0x40 | (channel << 1)
    # VREF=VDD，正常输出模式，立即更新，不写EEPROM
    i2c.writeto(
        dac_address,
        bytes([command, (code >> 8) & 0x0F, code & 0xFF])
    )

# ---------- ADS1256：使用你提供的接线 ----------
spi = SPI(
    2,
    baudrate=1_000_000,
    polarity=0,
    phase=1,
    sck=Pin(18),
    mosi=Pin(6),
    miso=Pin(19)
)

cs = Pin(5, Pin.OUT, value=1)
drdy = Pin(16, Pin.IN, pull=Pin.PULL_UP)

def wait_ready(timeout_ms=3000):
    start = time.ticks_ms()
    while drdy.value():
        if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
            raise RuntimeError(
                "ADS1256 DRDY timeout: check power and GPIO16."
            )
        time.sleep_ms(1)

def command(value):
    # WAKEUP、RESET等命令不能统一在发送前等待DRDY
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
    command(0x0F)       # SDATAC：退出连续读模式
    command(0xFE)       # RESET：软件复位
    time.sleep_ms(10)
    wait_ready()

    status = read_register(0)
    chip_id = status >> 4
    print("ADS1256 STATUS=0x%02X, ID=%d" % (status, chip_id))

    if chip_id != 3:
        raise RuntimeError("ADS1256 ID error: expected 3.")

    write_register(0, 0x02)  # MSB优先，开启缓冲，关闭自动校准
    write_register(2, 0x00)  # PGA=1，关闭检测电流源和CLKOUT
    write_register(3, 0x63)  # 50 SPS，假设晶振7.68MHz
    write_register(1, 0x01)  # A0-A1

    # 读回配置，防止通信异常时继续显示无效读数
    if (read_register(0) & 0x0E) != 0x02:
        raise RuntimeError("STATUS configuration failed.")
    if read_register(2) != 0x00:
        raise RuntimeError("ADCON configuration failed.")
    if read_register(3) != 0x63:
        raise RuntimeError("DRATE configuration failed.")

    command(0xF0)       # SELFCAL
    time.sleep_ms(5)
    wait_ready()
    print("ADS1256 calibrated: buffer ON, gain 1, 50 SPS.")

def read_voltage(positive, negative):
    mux = (positive << 4) | negative
    write_register(1, mux)

    # 换通道后重启转换，等待新通道的有效数据
    command(0xFC)       # SYNC
    command(0x00)       # WAKEUP
    time.sleep_ms(1)
    wait_ready()

    cs.value(0)
    try:
        spi.write(b"\x01")   # RDATA
        time.sleep_us(10)
        data = spi.read(3)
    finally:
        cs.value(1)

    # 手动解码24位二进制补码，兼容MicroPython
    raw = (data[0] << 16) | (data[1] << 8) | data[2]
    if raw & 0x800000:
        raw -= 1 << 24

    if raw >= 0x7FFFF0 or raw <= -0x7FFFF0:
        raise RuntimeError("ADC near full scale: check input voltage.")

    # PGA=1；这是ADC实测结果换算，不使用DAC设定值
    return raw * (2.0 * VREF) / 0x7FFFFF

def main():
    global dac_address

    if R_SHUNT is not None and R_SHUNT <= 0:
        raise ValueError("R_SHUNT must be positive or None.")
    if not 0 <= DAC_CODE <= 4095:
        raise ValueError("DAC_CODE must be 0..4095.")

    devices = i2c.scan()
    candidates = [a for a in devices if 0x60 <= a <= 0x67]
    if len(candidates) != 1:
        raise RuntimeError("Cannot uniquely identify MCP4728.")

    dac_address = candidates[0]
    print("MCP4728:", hex(dac_address))

    # 先清零，再初始化ADC
    for channel in range(4):
        dac_write(channel, 0)

    init_adc()

    dac_write(0, DAC_CODE)
    time.sleep_ms(300)

    print("Reading REAL voltages from ADS1256.")
    if R_SHUNT is None:
        print("R_SHUNT not set: current calculation disabled.")
    print("Ctrl+C to stop; normal cleanup will set DAC A to zero.")

    count = 0
    while True:
        # 两组电压依次采样，并非同时采样
        electrode_v = read_voltage(0, 1)
        resistor_v = read_voltage(2, 3)

        count += 1
        print("\n--- Measurement %d ---" % count)
        print("Electrode A0-A1: %+.6f V" % electrode_v)
        print("Shunt     A2-A3: %+.6f V" % resistor_v)

        if R_SHUNT is not None:
            current_uA = resistor_v / R_SHUNT * 1_000_000
            print("Current:         %+.3f uA" % current_uA)

        time.sleep_ms(500)

try:
    main()
except KeyboardInterrupt:
    print("\nStopped.")
except Exception as error:
    print("\nERROR:", error)
finally:
    cs.value(1)
    if dac_address is not None:
        try:
            dac_write(0, 0)
            print("DAC A set to 0V.")
        except Exception:
            print("Could not clear DAC A; disconnect circuit power.")
