# main.py — electrochem-platform ESP32 I–V 台架采集固件（MicroPython）
#
# 硬件：ESP32 台架板 + ADS1256（SPI）+ MCP4728（I2C）+ DS18B20（1-Wire）。引脚按树莓派上实测通过的
#       台架脚本（bench/，2026-10-10 同步）配置；MOSI 用 GPIO6，经典 ESP32 上那是 flash 引脚，板子应为 S3 一类。
# 测量：MCP4728 通道 B 输出中点电压作虚拟地，通道 A 在「中点 ± 幅值」间翻转，电池两端得到
#       双极性方波；ADS1256 在每个半周期的平台段交错采 U（电池两端）和 I（采样电阻压降 / R），
#       取「正半周均值 − 负半周均值」的一半作幅值，ADC/DAC 的恒定零点偏置在相减中抵消。
# 输出：USB 串口 115200，每帧一行 JSON（帧格式见 v2/docs/接口.md §5，字段名同原项目 docs/接入数据格式.md
#       的 Raw 层 + 溯源字段）；"# " 开头的行是日志，v2 把它显示在设备状态里。
# 接入：v2 用 EC_DEVICE=serial 直接读这个串口，质量标志随帧进入判稳；原项目经 capture_serial.py 存 CSV 回放。
# 边界：固件只产出 U/I/T 与 quality_flags，不算 G/κ/κ25——计算链在主机（v2 ec/chemistry.py，原项目 measurement.py）；
#       也不做通道校准，Raw 只按标称 VREF/PGA/R_SHUNT 换算，电池常数 Kcell 由平台用标准液标定。
#
# Thonny（树莓派）：解释器选 MicroPython (ESP32)、端口 /dev/ttyUSB0 或 /dev/ttyACM0 → 打开本文件 → 按实物改「配置」→
#   文件 → 另存为 → MicroPython 设备 → main.py → 点 Stop/Restart（或 Ctrl-D 软重启）即开始输出。
#   用 v2 前关掉 Thonny（或 运行 → 断开）：v2 打开串口时会发 Ctrl-C/Ctrl-B/Ctrl-D 让本程序重新运行。
# REPL 台架工具（先点 Stop 停止采集）：
#   import main
#   main.probe()        探测三个器件，不加激励
#   main.volts(0, 1)    读一次 AIN0−AIN1（8 = AINCOM）
#   main.dc(0.1)        电池两端加 +0.1 V 静态电压（只用于电阻负载 + 万用表核对，勿接溶液）
#   main.rest()         电池两端回 0 V
#   main.run(10)        采 10 帧后停止

import gc
import json
import time
import binascii
import machine
import onewire
import ds18x20
from machine import Pin, SPI, I2C

# ============================ 配置（按台架实物修改） ============================
FIRMWARE_VERSION = "0.1.0-mpy"
DEVICE_ID = None              # None = 按芯片 MAC 自动生成 "ESP32-IV-xxxxxx"，多块板无需逐块改

# ADS1256（SPI mode 1）。GPIO4 已被 DS18B20 占用，DRDY 用 16
ADS_SPI_ID = 2
ADS_SCK, ADS_MOSI, ADS_MISO, ADS_CS, ADS_DRDY = 18, 6, 19, 5, 16   # 与 bench/ads1256_probe.py 一致
ADS_BAUD = 1000000            # 上限 fCLKIN/4 ≈ 1.92 MHz
ADS_VREF_V = 2.5              # 模块板载基准
ADS_PGA = 1                   # 1/2/4/8/16/32/64，U、I 共用；满量程 ±2·VREF/PGA
ADS_DRATE_SPS = 1000          # 取值见 _DRATE 表；越高越快、噪声越大
ADS_BUFFER = True             # 输入缓冲：开 = 输入阻抗高，但各输入须在 0~3.0 V（AVDD=5 V 时）

# 差分通道（正端, 负端），0~7 = AIN0~AIN7，8 = AINCOM
U_CH = (0, 1)                 # 电池两端：AIN0 接电极 1（VA 侧），AIN1 接电极 2
I_CH = (2, 3)                 # 采样电阻两端：AIN2 接电极 2 侧，AIN3 接 VB 侧
R_SHUNT_OHM = 10000.0         # ★ 必须与实物一致：电流采样电阻阻值（Ω），I = V_shunt / R；台架现用 10 kΩ

# MCP4728（I2C）
I2C_ID, I2C_SCL, I2C_SDA, I2C_FREQ = 0, 9, 8, 100000   # 与 bench/mcp4728_test.py 一致（100 kHz 已实测）
DAC_ADDR = None               # None = 在 0x60~0x67 自动查找
DAC_VREF_V = 2.048            # 2.048 = 内部基准×1；4.096 = 内部基准×2（需 VDD≥4.5 V）；其他值 = 以 VDD 为基准
DAC_DRIVE_CH = 0              # 通道 A：激励驱动端 → 电极 1
DAC_VGND_CH = 1               # 通道 B：虚拟地（中点）→ 采样电阻下端

# 激励：双极性方波。按帧突发——每帧先跑 WARMUP_CYCLES 个预热周期（不计入），再跑 CYCLES_PER_FRAME
# 个计入周期，然后电池两端回 0 V，在静息时段输出本帧、读温度。
EXC_FREQ_HZ = 10.0            # 电阻负载可用；裸电极测溶液时频率越低极化误差越大，须台架验证（README 第 7 节）
EXC_AMPLITUDE_V = 0.2         # 幅值（VA−VB 的半峰峰值）；低频时界面承受大部分外加电压，溶液测量宜小
SETTLE_FRACTION = 0.6         # 每个半周期前 60% 等待稳定，之后才采样
N_PAIRS = 2                   # 每个半周期采 U/I 的对数，按 U I I U … 交错（取偶数时 U、I 时间重心重合）
WARMUP_CYCLES = 1             # 从静息启动的首周期不对称，丢弃
CYCLES_PER_FRAME = 4
FRAME_PERIOD_MS = 1000        # 帧周期 → 1 Hz 输出（v2 判稳窗口 30 s ≈ 30 帧）；原项目 CSV 回放时 EC_CSV_SAMPLE_RATE_HZ 要与之一致

# DS18B20
TEMP_PIN = 4                  # DQ，4.7 kΩ 上拉到 3V3
TEMP_MAX_AGE_MS = 3000        # 最近一次有效读数超过这个年龄即视为失效（帧内 null + TEMP_INVALID）
TEMP_RANGE_C = (-10.0, 100.0) # 溶液温度合理区间，超出视为无效

# 质量标志阈值（台架标定前的保守值）
OPEN_VSHUNT_V = 50e-6         # 采样电阻压降幅值低于此 → OPEN_CIRCUIT
SHORT_U_RATIO = 0.01          # U 幅值低于激励幅值的 1% → SHORT_CIRCUIT
ASYM_LIMIT = 0.10             # 正负半周 U 不对称度上限，超出 → WAVEFORM_UNSTABLE
DIAG = False                  # True = 帧内附带正/负半周分量 u_pos_v/u_neg_v/i_pos_a/i_neg_a（排障用）
# ==============================================================================

SCHEMA_VERSION = 2            # 帧字段词汇版本，与后端协议帧 schema_version 一致

_WAKEUP = b"\x00"
_RDATA = b"\x01"
_SDATAC = b"\x0f"
_SELFCAL = b"\xf0"
_SYNC = b"\xfc"
_RESET = b"\xfe"
_REG_STATUS, _REG_MUX, _REG_ADCON, _REG_DRATE = 0, 1, 2, 3
_SAT = 0x7FE000               # |码值| ≥ 满量程的 99.9% 视为饱和

# 数据率 → (DRATE 寄存器码, 切换通道后的建立时间 µs)，建立时间取自数据手册 Table 13
_DRATE = {
    30000: (0xF0, 210), 15000: (0xE0, 250), 7500: (0xD0, 310), 3750: (0xC0, 440),
    2000: (0xB0, 680), 1000: (0xA1, 1180), 500: (0x92, 2180), 100: (0x82, 10180),
    60: (0x72, 16840), 50: (0x63, 20180), 30: (0x53, 33510), 25: (0x43, 40180),
    15: (0x33, 66840), 10: (0x23, 100180), 5: (0x13, 200180),
}
_PGA = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4, 32: 5, 64: 6}


class HWError(Exception):
    pass


def _log(level, msg):
    print("# %s %s" % (level, msg))


def _hex(b):
    return binascii.hexlify(b).decode()


def _fmt(v):
    """浮点 → JSON 数字（7 位有效数字，与 ESP32 单精度浮点匹配）；NaN/Inf → null。"""
    if v - v != 0:
        return "null"
    s = "%.7g" % v
    if "." not in s and "e" not in s:
        s += ".0"
    return s


def _json_line(pairs):
    """按给定顺序拼一行紧凑 JSON（MicroPython 的 dict 不保证顺序）。"""
    parts = []
    for k, v in pairs:
        if v is None:
            s = "null"
        elif isinstance(v, float):
            s = _fmt(v)
        elif isinstance(v, str):
            s = json.dumps(v)
        else:
            s = str(v)
        parts.append('"%s":%s' % (k, s))
    return "{" + ",".join(parts) + "}"


def _mux_byte(pair):
    return (pair[0] << 4) | pair[1]


def _wait_until_us(deadline):
    d = time.ticks_diff(deadline, time.ticks_us())
    if d > 0:
        time.sleep_us(d)


def _sleep_until_ms(deadline):
    d = time.ticks_diff(deadline, time.ticks_ms())
    if d > 0:
        time.sleep_ms(d)


class _Mono:
    """ticks_ms 约 12 天回绕一次；累加成自 run() 启动起不回绕的毫秒数，作 monotonic_ms。"""

    def __init__(self):
        self._last = time.ticks_ms()
        self.ms = 0

    def now(self):
        t = time.ticks_ms()
        self.ms += time.ticks_diff(t, self._last)
        self._last = t
        return self.ms


class ADS1256:
    def __init__(self):
        self.cs = Pin(ADS_CS, Pin.OUT, value=1)
        self.drdy = Pin(ADS_DRDY, Pin.IN, Pin.PULL_UP)
        self.spi = SPI(ADS_SPI_ID, baudrate=ADS_BAUD, polarity=0, phase=1,
                       sck=Pin(ADS_SCK), mosi=Pin(ADS_MOSI), miso=Pin(ADS_MISO))
        self.drate_code, self.settle_us = _DRATE[ADS_DRATE_SPS]
        self.pre_wait_us = self.settle_us * 3 // 4      # 建立时间内不必轮询 DRDY
        self.timeout_us = self.settle_us * 4 + 5000
        self.lsb_v = 2.0 * ADS_VREF_V / ADS_PGA / 8388608
        self._rx = bytearray(3)
        self._mux = {}

    def _cmd(self, c):
        self.cs(0)
        self.spi.write(c)
        self.cs(1)

    def read_reg(self, reg):
        self.cs(0)
        self.spi.write(bytes((0x10 | reg, 0)))
        time.sleep_us(7)                                  # t6 ≥ 50/fCLKIN ≈ 6.5 µs
        v = self.spi.read(1, 0)[0]
        self.cs(1)
        return v

    def write_reg(self, reg, val):
        self.cs(0)
        self.spi.write(bytes((0x50 | reg, 0, val)))
        self.cs(1)

    def wait_drdy(self, timeout_us):
        t0 = time.ticks_us()
        while self.drdy.value():
            if time.ticks_diff(time.ticks_us(), t0) > timeout_us:
                return False
        return True

    def _start(self, mux):
        """写 MUX → SYNC → WAKEUP：数字滤波器从新通道重新开始，DRDY 拉低即为建立好的数据。"""
        cmd = self._mux.get(mux)
        if cmd is None:
            cmd = self._mux[mux] = bytes((0x50 | _REG_MUX, 0, mux))
        self._cmd(cmd)
        self._cmd(_SYNC)
        time.sleep_us(4)                                  # t11 ≥ 24/fCLKIN ≈ 3.1 µs
        self._cmd(_WAKEUP)

    def read_code(self, mux):
        """切到 mux 差分对，读一次建立好的转换结果（24 位有符号码值）。"""
        self._start(mux)
        time.sleep_us(self.pre_wait_us)
        if not self.wait_drdy(self.timeout_us):
            raise HWError("ADS1256 DRDY 超时")
        rx = self._rx
        self.cs(0)
        self.spi.write(_RDATA)
        time.sleep_us(7)
        self.spi.readinto(rx, 0)
        self.cs(1)
        v = (rx[0] << 16) | (rx[1] << 8) | rx[2]
        return v - 0x1000000 if v & 0x800000 else v

    def setup(self):
        """复位 → 配置 → 自校准 → 回读核对。返回 (成功?, 说明)。"""
        self._cmd(_RESET)
        time.sleep_ms(2)
        self.wait_drdy(500000)
        self._cmd(_SDATAC)
        status = self.read_reg(_REG_STATUS)
        if status >> 4 != 3:
            return False, ("ADS1256 无应答：STATUS=0x%02X（高 4 位 ID 应为 3）。0x00 = MISO 恒低，先查模块 5V 供电"
                           "（未供电时 ESD 钳位会让所有线读 0），再查 DOUT→GPIO%d、CS→GPIO%d；0xFF = MISO 恒高，"
                           "查接线与 SPI 模式" % (status, ADS_MISO, ADS_CS))
        st = 0x02 if ADS_BUFFER else 0x00                 # ORDER=MSB 在先，ACAL 关，BUFEN
        adcon = _PGA[ADS_PGA]                             # CLKOUT 关，传感器检测电流关
        self.write_reg(_REG_STATUS, st)
        self.write_reg(_REG_ADCON, adcon)
        self.write_reg(_REG_DRATE, self.drate_code)
        want = (st, adcon, self.drate_code)
        got = (self.read_reg(_REG_STATUS) & 0x0E, self.read_reg(_REG_ADCON), self.read_reg(_REG_DRATE))
        if got != want:
            return False, "ADS1256 寄存器写读不一致：写 %r，读回 %r" % (want, got)
        self._cmd(_SELFCAL)
        time.sleep_us(100)
        if not self.wait_drdy(2000000):
            return False, "ADS1256 自校准超时：DRDY（GPIO%d）一直不拉低" % ADS_DRDY
        # 同步后 DRDY 应先拉高、约 settle_us 后才拉低；立刻就是低 = DRDY 线恒低，读数会提前
        self._start(_mux_byte(U_CH))
        t0 = time.ticks_us()
        self.wait_drdy(self.timeout_us)
        dt = time.ticks_diff(time.ticks_us(), t0)
        note = ""
        if dt < self.settle_us // 4:
            note = "；警告：同步后 %d µs 就读到 DRDY 为低（应约 %d µs），查 DRDY→GPIO%d 是否短路到地" % (
                dt, self.settle_us, ADS_DRDY)
        return True, "ADS1256 正常：STATUS=0x%02X ID=3，%d SPS，PGA=%d，缓冲%s，已自校准%s" % (
            status, ADS_DRATE_SPS, ADS_PGA, "开" if ADS_BUFFER else "关", note)


class MCP4728:
    def __init__(self, i2c, addr):
        self.i2c = i2c
        self.addr = addr
        if abs(DAC_VREF_V - 2.048) < 1e-6:
            self.cfg = 0x80           # 内部基准 2.048 V，增益 ×1
        elif abs(DAC_VREF_V - 4.096) < 1e-6:
            self.cfg = 0x90           # 内部基准，增益 ×2
        else:
            self.cfg = 0x00           # 以 VDD 为基准，DAC_VREF_V 填实测 VDD
        self.lsb_v = DAC_VREF_V / 4096

    def cmd(self, ch, code):
        """Multi-Write 单通道 3 字节：UDAC=0 应答后立即更新输出，只写输入寄存器、不写 EEPROM。"""
        return bytes((0x40 | (ch << 1), self.cfg | (code >> 8), code & 0xFF))

    def write(self, buf):
        self.i2c.writeto(self.addr, buf)

    def readback(self):
        """回读 4 个通道输入寄存器：[(码值, VREF/PD/增益位), ...]。"""
        d = self.i2c.readfrom(self.addr, 24)
        return [(((d[b + 1] & 0x0F) << 8) | d[b + 2], d[b + 1] & 0xF0) for b in (0, 6, 12, 18)]


class TempProbe:
    CONV_MS = 800                     # 12 位转换 750 ms + 余量

    def __init__(self, pin):
        self.ds = ds18x20.DS18X20(onewire.OneWire(Pin(pin)))
        self.rom = None
        self.value = None
        self.value_ms = 0
        self.conv_ms = None
        self.scan_ms = None
        self.fails = 0

    def _scan(self):
        try:
            roms = self.ds.scan()
        except Exception:
            roms = []
        self.rom = roms[0] if roms else None
        self.conv_ms = None
        return self.rom

    def _read(self):
        """读上一轮转换结果。CRC 错 / 全 0（DQ 短路也能过 CRC）/ 85 °C 上电值 / 超出合理区间 → 抛异常。"""
        rom = self.rom
        if rom[0] in (0x28, 0x22):
            buf = self.ds.read_scratch(rom)
            if buf[4] & 0x9F != 0x1F:                     # 配置寄存器固定位
                raise ValueError("scratchpad 异常 %s" % _hex(buf))
            raw = buf[0] | (buf[1] << 8)
            if raw & 0x8000:
                raw -= 0x10000
            if raw == 0x0550:
                raise ValueError("85 °C 上电默认值（转换未完成或掉电复位）")
            t = raw / 16
        else:
            t = self.ds.read_temp(rom)
        if not TEMP_RANGE_C[0] <= t <= TEMP_RANGE_C[1]:
            raise ValueError("%.4f °C 超出合理区间" % t)
        return t

    def prime(self):
        """启动时阻塞读一次，让第一帧就带温度。返回温度或 None。"""
        if self._scan() is None:
            return None
        try:
            self.ds.convert_temp()
            time.sleep_ms(self.CONV_MS)
            self.value = self._read()
            self.value_ms = time.ticks_ms()
        except Exception as e:
            _log("WARN", "DS18B20 首次读数失败：%r" % e)
            return None
        return self.value

    def poll(self, now):
        """每帧静息时段调一次：到点就读结果并启动下一轮转换；探头丢失则每 5 s 重扫。"""
        if self.rom is None:
            if self.scan_ms is not None and time.ticks_diff(now, self.scan_ms) < 5000:
                return
            self.scan_ms = now
            if self._scan() is None:
                return
            _log("INFO", "DS18B20 已找到：rom=%s" % _hex(self.rom))
        try:
            if self.conv_ms is not None:
                if time.ticks_diff(now, self.conv_ms) < self.CONV_MS:
                    return
                self.value = self._read()
                self.value_ms = now
                self.fails = 0
            self.ds.convert_temp()
            self.conv_ms = now
        except Exception as e:
            self.conv_ms = None
            self.fails += 1
            if self.fails >= 3:
                _log("WARN", "DS18B20 连续 %d 次读取失败（%r），重新扫描总线" % (self.fails, e))
                self.rom = None
                self.scan_ms = None
                self.fails = 0

    def current(self, now):
        if self.value is None or time.ticks_diff(now, self.value_ms) > TEMP_MAX_AGE_MS:
            return None
        return self.value


class Bench:
    def __init__(self):
        self.device_id = DEVICE_ID or ("ESP32-IV-" + _hex(machine.unique_id()[-3:]).upper())
        self.range_id = "RS%gR_G%d" % (R_SHUNT_OHM, ADS_PGA)
        self.mux_u = _mux_byte(U_CH)
        self.mux_i = _mux_byte(I_CH)
        self.half_us = int(500000 / EXC_FREQ_HZ)
        self.settle_wait_us = int(self.half_us * SETTLE_FRACTION)
        self.adc = None
        self.dac = None
        self.temp = None
        self._warned_sign = False

    def init(self):
        """依次初始化 DAC（先把电池两端置 0 V）、ADC、温度探头。ADC/DAC 任一不可用返回 False。"""
        ok = self._init_dac()
        ok = self._init_adc() and ok
        self._init_temp()
        return ok

    def _init_dac(self):
        self.dac = None
        try:
            i2c = I2C(I2C_ID, scl=Pin(I2C_SCL), sda=Pin(I2C_SDA), freq=I2C_FREQ)
            found = i2c.scan()
        except OSError as e:
            _log("ERROR", "I2C 初始化失败：%r" % e)
            return False
        cands = [a for a in found if 0x60 <= a <= 0x67]
        addr = DAC_ADDR if DAC_ADDR is not None else (cands[0] if cands else None)
        if addr is None or addr not in found:
            _log("ERROR", "未找到 MCP4728（0x60~0x67）：I2C scan=%s。查 SDA→GPIO%d、SCL→GPIO%d、VDD/GND 与上拉电阻"
                 % ([hex(a) for a in found], I2C_SDA, I2C_SCL))
            return False
        dac = MCP4728(i2c, addr)
        mid = 2048
        amp = int(round(EXC_AMPLITUDE_V / dac.lsb_v))
        self.mid = mid
        self.mid_v = mid * dac.lsb_v
        self.amp_v = amp * dac.lsb_v
        self.buf_drive = (dac.cmd(DAC_DRIVE_CH, mid + amp), dac.cmd(DAC_DRIVE_CH, mid - amp))
        self.buf_rest = dac.cmd(DAC_DRIVE_CH, mid) + dac.cmd(DAC_VGND_CH, mid)
        try:
            dac.write(self.buf_rest)
            got = dac.readback()
        except OSError as e:
            _log("ERROR", "MCP4728（0x%02X）通信失败：%r" % (addr, e))
            return False
        self.dac = dac
        want = (mid, dac.cfg)
        if got[DAC_DRIVE_CH] != want or got[DAC_VGND_CH] != want:
            _log("WARN", "MCP4728 回读不一致：期望 %r，读到 A=%r B=%r（0x%02X 真是 MCP4728 吗？）"
                 % (want, got[DAC_DRIVE_CH], got[DAC_VGND_CH], addr))
        else:
            _log("INFO", "MCP4728 正常：addr=0x%02X，VA=VB=%.3f V（电池两端 0 V）" % (addr, self.mid_v))
        if ADS_BUFFER and self.mid_v + self.amp_v > 3.0:
            _log("WARN", "激励峰值 %.3f V 超过 ADS1256 缓冲输入上限 3.0 V，读数会失真" % (self.mid_v + self.amp_v))
        return True

    def _init_adc(self):
        self.adc = None
        try:
            adc = ADS1256()
            ok, msg = adc.setup()
        except (OSError, ValueError) as e:
            ok, msg = False, "ADS1256 初始化异常：%r" % e
        _log("INFO" if ok else "ERROR", msg)
        if ok:
            self.adc = adc
        return ok

    def _init_temp(self):
        self.temp = TempProbe(TEMP_PIN)
        t = self.temp.prime()
        if t is None:
            _log("WARN", "DS18B20 未就绪：帧内温度为 null 并带 TEMP_INVALID，每 5 s 自动重扫。"
                 "查 DQ→GPIO%d、4.7kΩ 上拉到 3V3、VCC/GND" % TEMP_PIN)
        else:
            _log("INFO", "DS18B20 正常：rom=%s，T=%.4f °C" % (_hex(self.temp.rom), t))

    def safe(self):
        """电池两端回 0 V（VA=VB=中点）。所有退出路径都会调用。"""
        if self.dac is not None:
            try:
                self.dac.write(self.buf_rest)
            except OSError:
                pass

    def check_timing(self):
        """实测单次读数耗时，确认半周期放得下「建立等待 + 采样」、帧周期放得下一帧激励。"""
        t0 = time.ticks_us()
        for _ in range(4):
            self.adc.read_code(self.mux_u)
        per_read = time.ticks_diff(time.ticks_us(), t0) // 4
        need = self.settle_wait_us + 2 * N_PAIRS * per_read * 5 // 4 + 1000
        burst_ms = (WARMUP_CYCLES + CYCLES_PER_FRAME) * 2 * self.half_us // 1000
        ok = True
        if need > self.half_us:
            _log("ERROR", "激励频率过高：半周期 %d µs 放不下 建立 %d µs + 采样 %d×%d µs。"
                 "降低 EXC_FREQ_HZ / N_PAIRS / SETTLE_FRACTION，或提高 ADS_DRATE_SPS"
                 % (self.half_us, self.settle_wait_us, 2 * N_PAIRS, per_read))
            ok = False
        if burst_ms + 150 > FRAME_PERIOD_MS:
            _log("ERROR", "FRAME_PERIOD_MS=%d 太短：一帧激励就要 %d ms，另需约 150 ms 输出与读温度"
                 % (FRAME_PERIOD_MS, burst_ms))
            ok = False
        if ok:
            _log("INFO", "时序：单次读数 %d µs；半周期 %d µs（前 %d µs 等稳定）；每帧激励 %d ms / 帧周期 %d ms"
                 % (per_read, self.half_us, self.settle_wait_us, burst_ms, FRAME_PERIOD_MS))
        return ok

    def burst(self):
        """一帧的双极性方波激励 + 采样。返回 (u_pos, u_neg, i_pos, i_neg, 饱和?, 超时?)，单位 V / A。"""
        if self.adc is None or self.dac is None:
            raise HWError("ADC/DAC 未就绪")
        read = self.adc.read_code
        write = self.dac.write
        mu = self.mux_u
        mi = self.mux_i
        half = self.half_us
        settle = self.settle_wait_us
        su = [0, 0]                   # 下标 0 = 正半周，1 = 负半周；累加整数码值，最后一次换算
        si = [0, 0]
        sat = False
        overrun = False
        t = time.ticks_us()
        for cyc in range(WARMUP_CYCLES + CYCLES_PER_FRAME):
            rec = cyc >= WARMUP_CYCLES
            for pol in (0, 1):
                write(self.buf_drive[pol])
                _wait_until_us(time.ticks_add(t, settle))
                if rec:
                    for k in range(N_PAIRS):
                        if k & 1:
                            ci = read(mi)
                            cu = read(mu)
                        else:
                            cu = read(mu)
                            ci = read(mi)
                        su[pol] += cu
                        si[pol] += ci
                        if abs(cu) >= _SAT or abs(ci) >= _SAT:
                            sat = True
                end = time.ticks_add(t, half)
                late = time.ticks_diff(time.ticks_us(), end)
                if late > 0:          # 采样拖过了半周期终点：从现在起算下一个半周期，并标记
                    t = time.ticks_us()
                    if late > 200:
                        overrun = True
                else:
                    _wait_until_us(end)
                    t = end
        write(self.buf_rest)
        kv = self.adc.lsb_v / (CYCLES_PER_FRAME * N_PAIRS)
        ki = kv / R_SHUNT_OHM
        return su[0] * kv, su[1] * kv, si[0] * ki, si[1] * ki, sat, overrun

    def frame_line(self, seq, t_ms, meas, temp):
        flags = []
        u = i = None
        extra = []
        if meas is None:
            flags.append("DROPOUT")
        else:
            u_pos, u_neg, i_pos, i_neg, sat, unstable = meas
            u = (u_pos - u_neg) / 2
            i = (i_pos - i_neg) / 2
            if sat:
                flags.append("SATURATED")
            if abs(i) * R_SHUNT_OHM < OPEN_VSHUNT_V:
                flags.append("OPEN_CIRCUIT")
            elif abs(u) < self.amp_v * SHORT_U_RATIO:
                flags.append("SHORT_CIRCUIT")
            else:
                if abs(u_pos + u_neg) > ASYM_LIMIT * (abs(u_pos) + abs(u_neg)):
                    unstable = True
                if (u < 0 or i < 0) and not self._warned_sign:
                    self._warned_sign = True
                    _log("WARN", "U 或 I 为负：检查电池/采样电阻接线方向，或对调 U_CH / I_CH 的正负端"
                         "（主机判为接线反：v2 标 POLARITY，原项目标 COMPUTE_INVALID）")
            if unstable:
                flags.append("WAVEFORM_UNSTABLE")
            if DIAG:
                extra = [("u_pos_v", u_pos), ("u_neg_v", u_neg), ("i_pos_a", i_pos), ("i_neg_a", i_neg)]
        if temp is None:
            flags.append("TEMP_INVALID")
        return _json_line([
            ("schema_version", SCHEMA_VERSION),
            ("seq_no", seq),
            ("monotonic_ms", t_ms),
            ("voltage_raw_v", u),
            ("current_raw_a", i),
            ("temperature_raw_c", temp),
            ("quality_flags", "|".join(flags) if flags else None),
            ("device_id", self.device_id),
            ("firmware_version", FIRMWARE_VERSION),
            ("range_id", self.range_id),
            ("excitation_frequency_hz", float(EXC_FREQ_HZ)),
            ("excitation_amplitude_v", self.amp_v),
        ] + extra)


def _config_errors():
    e = []
    if ADS_PGA not in _PGA:
        e.append("ADS_PGA 须为 %s 之一" % sorted(_PGA))
    if ADS_DRATE_SPS not in _DRATE:
        e.append("ADS_DRATE_SPS 须为 %s 之一" % sorted(_DRATE))
    for name, pair in (("U_CH", U_CH), ("I_CH", I_CH)):
        if len(pair) != 2 or not (0 <= pair[0] <= 8 and 0 <= pair[1] <= 8) or pair[0] == pair[1]:
            e.append("%s 须为两个不同的 0~8（8 = AINCOM）" % name)
    if not R_SHUNT_OHM > 0:
        e.append("R_SHUNT_OHM 须 > 0")
    if not EXC_FREQ_HZ > 0:
        e.append("EXC_FREQ_HZ 须 > 0")
    if not 0 < SETTLE_FRACTION < 1:
        e.append("SETTLE_FRACTION 须在 0~1 之间")
    if N_PAIRS < 1 or CYCLES_PER_FRAME < 1 or WARMUP_CYCLES < 0:
        e.append("N_PAIRS、CYCLES_PER_FRAME 须 ≥ 1，WARMUP_CYCLES 须 ≥ 0")
    lsb = DAC_VREF_V / 4096
    if not 1 <= int(round(EXC_AMPLITUDE_V / lsb)) <= 2047:
        e.append("EXC_AMPLITUDE_V 须在 %.4f~%.3f V" % (lsb, 2047 * lsb))
    if DAC_DRIVE_CH == DAC_VGND_CH or not (0 <= DAC_DRIVE_CH <= 3 and 0 <= DAC_VGND_CH <= 3):
        e.append("DAC_DRIVE_CH / DAC_VGND_CH 须为 0~3 且不同")
    return e


def run(max_frames=None):
    """主循环：每 FRAME_PERIOD_MS 输出一行 JSON 帧。max_frames=None 一直采，Ctrl-C 停止。"""
    errs = _config_errors()
    if errs:
        for m in errs:
            _log("ERROR", "配置错误：" + m)
        return
    b = Bench()
    _log("INFO", "electrochem ESP32 I-V 固件 %s，device_id=%s，range_id=%s"
         % (FIRMWARE_VERSION, b.device_id, b.range_id))
    interrupted = False
    try:
        while not b.init():
            b.safe()
            _log("WARN", "器件未就绪，5 s 后重试（Ctrl-C 退出）")
            time.sleep_ms(5000)
        try:
            timing_ok = b.check_timing()
        except HWError as e:
            _log("ERROR", "时序自检失败：%r" % e)
            timing_ok = False
        if not timing_ok:
            return
        _log("INFO", "开始输出：双极性方波 %g Hz、幅值 %.4f V（VB=%.3f V 虚拟地），每帧 %d 预热 + %d 计入周期，"
             "帧周期 %d ms。Ctrl-C 停止" % (EXC_FREQ_HZ, b.amp_v, b.mid_v, WARMUP_CYCLES, CYCLES_PER_FRAME,
                                            FRAME_PERIOD_MS))
        mono = _Mono()
        seq = 0
        fails = 0
        next_ms = time.ticks_ms()
        while max_frames is None or seq < max_frames:
            _sleep_until_ms(next_ms)
            t_ms = mono.now()
            seq += 1
            try:
                meas = b.burst()
                fails = 0
            except (OSError, HWError) as e:
                b.safe()
                meas = None
                fails += 1
                _log("ERROR", "第 %d 帧采样失败：%r" % (seq, e))
            now = time.ticks_ms()
            print(b.frame_line(seq, t_ms, meas, b.temp.current(now)))
            b.temp.poll(now)
            if fails >= 3:
                _log("WARN", "连续 %d 帧失败，重新初始化器件" % fails)
                b.init()
                fails = 0
            gc.collect()
            next_ms = time.ticks_add(next_ms, FRAME_PERIOD_MS)
            if time.ticks_diff(time.ticks_ms(), next_ms) > 0:
                next_ms = time.ticks_ms()        # 跟不上就重新对齐，不补帧
    except KeyboardInterrupt:
        interrupted = True
    finally:
        b.safe()                                # 先回 0 V 再打日志：Thonny 可能连发 Ctrl-C
        _log("INFO", ("已停止（Ctrl-C），" if interrupted else "") + "电池两端已回 0 V")


# ---------------- REPL 台架工具 ----------------
_b = None


def _bench():
    global _b
    if _b is None:
        _b = Bench()
        _b.init()
    return _b


def probe():
    """探测三个器件（不加激励），打印结果与排查提示。返回 ADC/DAC 是否就绪。"""
    global _b
    _b = Bench()
    ok = _b.init()
    _log("INFO" if ok else "ERROR", "探测结论：%s" % ("ADC/DAC 就绪" if ok else "有器件未就绪，按上面的提示排查"))
    return ok


def volts(p, n=8):
    """读一次 AINp − AINn（V），n=8 即对 AINCOM。"""
    b = _bench()
    if b.adc is None:
        _log("ERROR", "ADS1256 未就绪")
        return None
    v = b.adc.read_code((p << 4) | n) * b.adc.lsb_v
    _log("INFO", "AIN%d-%s = %.6f V" % (p, "AINCOM" if n == 8 else "AIN%d" % n, v))
    return v


def dc(v):
    """电池两端加静态电压 v（VA=中点+v，VB=中点）。溶液会电解/极化，只用于电阻负载 + 万用表核对。"""
    b = _bench()
    if b.dac is None:
        _log("ERROR", "MCP4728 未就绪")
        return None
    code = int(round(v / b.dac.lsb_v))
    if not -b.mid <= code <= 4095 - b.mid:
        _log("ERROR", "超出 DAC 范围：%.3f ~ %.3f V" % (-b.mid * b.dac.lsb_v, (4095 - b.mid) * b.dac.lsb_v))
        return None
    b.dac.write(b.dac.cmd(DAC_DRIVE_CH, b.mid + code) + b.dac.cmd(DAC_VGND_CH, b.mid))
    _log("WARN", "电池两端静态 %.4f V（用完执行 rest()）" % (code * b.dac.lsb_v))
    return code * b.dac.lsb_v


def rest():
    """电池两端回 0 V。"""
    _bench().safe()
    _log("INFO", "电池两端已回 0 V")


if __name__ == "__main__":
    run()
