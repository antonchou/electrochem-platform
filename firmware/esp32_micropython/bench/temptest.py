from machine import Pin
import onewire, ds18x20, time

ow = onewire.OneWire(Pin(4))          # DQ → GPIO4,4.7kΩ 上拉到 3V3
ds = ds18x20.DS18X20(ow)
roms = ds.scan()
print("Found %d device(s):" % len(roms), roms)

while True:
    ds.convert_temp()
    time.sleep_ms(750)                # 12 位转换最长 750ms；没等够读到的是上一次结果，冷启动时是 85°C 上电值
    for rom in roms:
        try:
            print("T = %.2f C" % ds.read_temp(rom))
        except Exception as e:        # CRC 错等：打印后继续，不让循环退出
            print("读取失败:", e)
    time.sleep_ms(250)