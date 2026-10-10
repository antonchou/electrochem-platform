# firmware — ESP32 固件

| 目录 | 状态 | 说明 |
|---|---|---|
| [`esp32_micropython/`](esp32_micropython/README.md) | 台架版（MicroPython，ESP32-S3；`main.py` 未上板，上板前须先改采样电阻接线） | ADS1256 + MCP4728 + DS18B20：双极性方波激励、U/I/T 采样与质量标志，每帧一行 JSON（字段对齐 `docs/接入数据格式.md`）；树莓派上用 Thonny 烧录；v2 用 `EC_DEVICE=serial` 直接读串口，附串口采集脚本（产出 v2 可回放的 `.jsonl` 和原项目 `EC_DRIVER=csv` 可回放的 CSV）；`bench/` 是单器件探测脚本（直流测量脚本已退役），审查与决策记录见 [`bench/审查报告.md`](esp32_micropython/bench/审查报告.md) |

正式的 ESP32-S3 实时固件（Phase 9 / N7）仍待开发，目标：

- 测量驱动：DS18B20（温度）、受控激励、电压采集、电流采集与 ADC；pH 电极为后续独立通道
- 固件同步采集 U/I/T，记录激励频率、幅值和量程，并产生饱和、开短路、温度无效等质量标志
- 与 backend 的通信协议对齐 `docs/接口说明.md`（帧含 seq/UTC/monotonic_ms，rule 38）；计算边界见 `docs/电导率I-V测量链路与开发路线.md`
- 设备驱动遵循统一 Driver Base Class（rule 34）
