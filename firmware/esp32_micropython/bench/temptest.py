from machine import Pin
import onewire, ds18x20, time

ow = onewire.OneWire(Pin(4))          # DQ → GPIO4,4.7kΩ 上拉到 3V3
ds = ds18x20.DS18X20(ow)
roms = ds.scan()
print("Found %d device(s):" % len(roms), roms)

while True:
    ds.convert_temp()
    time.sleep_ms(100)                # DS18B20 一次转换最长需 750ms
    for rom in roms:
        print("T = %.2f C" % ds.read_temp(rom))
    time.sleep(1)