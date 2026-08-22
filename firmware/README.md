# firmware — ESP32-S3 固件（占位）

本阶段（Phase 3/7）不含固件。后续阶段在此开发 ESP32-S3 采集固件：

- 测量驱动：DS18B20（温度）、受控激励、电压采集、电流采集与 ADC；pH 电极为后续独立通道
- 固件同步采集 U/I/T，记录激励频率、幅值和量程，并产生饱和、开短路、温度无效等质量标志
- 与 backend 的通信协议对齐 `docs/接口说明.md`（帧含 seq/UTC/monotonic_ms，rule 38）；计算边界见 `docs/电导率I-V测量链路与开发路线.md`
- 设备驱动遵循统一 Driver Base Class（rule 34）
