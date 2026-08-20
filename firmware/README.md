# firmware — ESP32-S3 固件（占位）

本阶段（Phase 3/7）不含固件。后续阶段在此开发 ESP32-S3 采集固件：

- 传感器驱动：DS18B20（温度）、BA121S / CM2（EC）、pH 电极（后续）
- 与 backend 的通信协议对齐 `docs/接口说明.md`（帧含 seq/UTC/monotonic_ms，rule 38）
- 设备驱动遵循统一 Driver Base Class（rule 34）
