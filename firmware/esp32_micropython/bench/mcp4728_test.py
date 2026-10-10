from machine import Pin, SoftI2C
import time

# 接线：SDA -> GPIO8，SCL -> GPIO9
i2c = SoftI2C(
    sda=Pin(8),
    scl=Pin(9),
    freq=100000
)

# 按 3.3V 电源计算理论电压
VDD = 3.3

# 四路输出设定值：整数，范围 0～4095
# 顺序：A、B、C、D
CODES = [1024, 0, 0, 0]


def find_dac():
    devices = i2c.scan()
    print("I2C devices:", [hex(a) for a in devices])

    # MCP4728 的地址范围；常见为 0x60 或 0x64
    candidates = [a for a in devices if 0x60 <= a <= 0x67]

    if not candidates:
        raise RuntimeError(
            "MCP4728 not found. Check power, SDA=8, SCL=9."
        )

    if len(candidates) > 1:
        raise RuntimeError(
            "Multiple candidate devices found; specify DAC address."
        )

    return candidates[0]


def set_channel(address, channel, code):
    if channel not in (0, 1, 2, 3):
        raise ValueError("Channel must be 0, 1, 2 or 3")

    if not isinstance(code, int) or not 0 <= code <= 4095:
        raise ValueError("Code must be an integer from 0 to 4095")

    # Multi-write：只更新寄存器，不写 EEPROM。
    # VREF=VDD，正常工作模式，UDAC=0（更新输出）。
    command = 0x40 | (channel << 1)
    high_byte = (code >> 8) & 0x0F
    low_byte = code & 0xFF

    i2c.writeto(
        address,
        bytes([command, high_byte, low_byte])
    )


def main():
    address = find_dac()
    print("Using MCP4728 at", hex(address))

    for channel, code in enumerate(CODES):
        set_channel(address, channel, code)

    print("Outputs configured.")
    print("Voltage below is calculated, NOT measured.")
    print("Press Ctrl+C to stop printing.")
    print("DAC outputs remain set after stopping.")
    print()

    count = 0

    while True:
        count += 1
        print("--- Sample %d ---" % count)

        for name, code in zip("ABCD", CODES):
            voltage = VDD * code / 4096
            print(
                "%s: code=%4d, estimated=%.3f V"
                % (name, code, voltage)
            )

        print()
        time.sleep(1)


try:
    main()
except KeyboardInterrupt:
    print("\nStopped. DAC outputs remain set.")
except OSError as error:
    print("I2C communication error:", error)
    print("Check wiring and power.")