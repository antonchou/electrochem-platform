# configs — 集中配置（rule 35/36）

配置、协议、实验模板与数据 schema 统一在这里**版本化**管理；
后端地址、串口号等**禁止散落硬编码**到业务代码。

## 目录规划

```
configs/
├── devices/      # 设备驱动配置（Mock / BA121S / CM2 / DS18B20，rule 34 统一接口）
├── experiments/  # 实验模板（样品梯度、测量参数）
└── calibration/  # 校准记录（标准液、系数、批次）
```

## 约定

- 每个配置带 `schema_version` 与 `created_at`，变更走迁移脚本（`scripts/`），不手工改表。
- 敏感字段（Wi-Fi 密码、令牌、私钥）**禁止**放这里——放 `.env` / 机密存储，且不入 Git（rule 37）。

## 示例

```json
{
  "schema_version": 1,
  "device": {
    "id": "mock-ec-01",
    "driver": "MockECDriver",
    "sensor_path_id": "CM2_WIDE",
    "unit": "us_cm",
    "sample_rate_hz": 10
  },
  "experiment": {
    "template_id": "nacl-gradient",
    "samples": ["BLANK", "NACL_002", "NACL_004", "NACL_008"],
    "duration_s": 300
  }
}
```
