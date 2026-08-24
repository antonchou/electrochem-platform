# configs — 集中配置（rule 35/36）

配置、协议、实验模板与数据 schema 统一在这里**版本化**管理；
后端地址、串口号等**禁止散落硬编码**到业务代码。

## 目录规划

```
configs/
├── devices/      # 设备驱动配置（含 mock.example.json）
├── experiments/  # 实验模板（样品梯度、测量参数）
└── calibration/  # 校准记录（标准液、系数、批次）
```

## 约定

- 每个配置带 `schema_version` 与 `created_at`，变更走迁移脚本（`scripts/`），不手工改表。
- 敏感字段（Wi-Fi 密码、令牌、私钥）**禁止**放这里——放 `.env` / 机密存储，且不入 Git（rule 37）。

## 示例

```json
{
  "schema_version": "1.0.0",
  "driver": "mock",
  "scenario": "stable",
  "seed": 2026,
  "sample_rate_hz": 10.0,
  "base_ec": 1413.0,
  "base_temperature": 25.0
}
```

完整可加载示例见 `configs/devices/mock.example.json`（扁平字段，与 `MockDeviceConfig.from_mapping` 一致）。

Mock 后端可通过 `EC_MOCK_CONFIG=configs/devices/mock.example.json` 加载完整配置；
`EC_MOCK_SCENARIO`、`EC_SAMPLE_RATE_HZ`、`EC_MOCK_SEED` 可覆盖常用运行参数。
