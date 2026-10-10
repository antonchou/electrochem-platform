# ads1256_probe.py —— 在 ESP32 MicroPython 上运行，树莓派经 Thonny/mpremote 下发
from machine import Pin, SPI
import time

# VSPI 默认引脚；DRDY=16（GPIO4 已被 DS18B20 占用）
spi = SPI(2, baudrate=1_000_000, polarity=0, phase=1,   # ADS1256 是 SPI mode1
          sck=Pin(18), mosi=Pin(6), miso=Pin(19))
cs = Pin(5, Pin.OUT, value=1)
drdy = Pin(16, Pin.IN, pull=Pin.PULL_UP)

VREF = 2.5   # 紫色模块板载 2.5V 基准，若用 2.048V 改这里

def wait_drdy(ms=2000):
    t0 = time.ticks_ms()
    while drdy.value():
        if time.ticks_diff(time.ticks_ms(), t0) > ms:
            return False
    return True

def rreg(addr):
    wait_drdy()
    cs.value(0)
    spi.write(bytes([0x10 | addr, 0x00]))
    time.sleep_us(50)                      # t6 ≥ 50·TCLKIN ≈ 6.5µs
    v = spi.read(1)[0]
    cs.value(1)
    return v

def wreg(addr, val):
    wait_drdy()
    cs.value(0)
    spi.write(bytes([0x50 | addr, 0x00, val]))
    time.sleep_us(50)
    cs.value(1)

def xfer(cmd):
    wait_drdy()
    cs.value(0)
    spi.write(bytes([cmd]))
    cs.value(1)

# ---- 0. 唤醒 + 复位（复位会自动触发自校准，DRDY 拉低即完成）----
xfer(0x00)                                  # WAKEUP（防上次停在待机）
time.sleep_ms(5)
xfer(0xFE)                                  # RESET
print('复位后 DRDY 拉低:', wait_drdy(1500))

# ---- 1. 器件在位：STATUS 高 4 位 = ID，ADS1256 应为 3 ----
st = rreg(0)
print('STATUS = 0x%02X, ID = %d（期望 3）' % (st, st >> 4))

# ---- 2. 寄存器写读回路 ----
wreg(2, 0x00)                               # ADCON: 时钟输出关、传感器检测电流关、PGA=1（复位值是 0x20，读回 0x00 才说明写入生效）
wreg(1, 0x01)                               # MUX: AIN0 对 AINCOM
print('ADCON = 0x%02X（期望 0x00）, MUX = 0x%02X（期望 0x01）' % (rreg(2), rreg(1)))

# ---- 3. 自校准 + 读转换值（AIN0 短接 GND 应 ≈0V）----
xfer(0xF0)                                  # SELFCAL
print('自校准完成:', wait_drdy(2000))
wait_drdy()
cs.value(0)
spi.write(b'\x01')                          # RDATA
time.sleep_us(50)
d = spi.read(3)                             # 24 位二进制补码，MSB 在前
cs.value(1)
raw = (d[0] << 16) | (d[1] << 8) | d[2]     # MicroPython 的 int.from_bytes 不支持 signed，手动补码
if raw & 0x800000:
    raw -= 1 << 24
print('raw = %d, U = %.6f V (AIN0-AINCOM)' % (raw, raw * (2 * VREF) / 0x7FFFFF))