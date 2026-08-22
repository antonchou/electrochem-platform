# backend — FastAPI 后端（模拟数据源 → 真实设备驱动）

提供实时数据流、实验控制、历史查询与导出（Phase 7）。当前为模拟数据源；
**真实设备接入后，前端仅需改连接地址，核心代码不变**（驱动按统一 Driver Base Class 替换，rule 34）。

## 目录

```
backend/
├── app/
│   ├── main.py          # FastAPI 入口（lifespan 启停持久化）
│   ├── routes.py        # WS 实时流 + REST 控制 + 历史/导出
│   ├── broadcast.py     # 每客户端独立有界队列，隔离慢连接
│   ├── drivers/         # DeviceDriver 接口 + 可配置 MockDevice
│   ├── schemas.py       # 协议模型
│   ├── state.py         # 实验状态机 + 溯源上下文
│   ├── stream.py        # 模拟数据发生器
│   ├── storage.py       # SQLite 存储（append-only 约束）
│   └── persistence.py   # 后台异步落库
├── tests/               # pytest（存储/协议/API）
├── requirements.txt
└── README.md
```

## 启动

```bash
python3 -m venv .venv
.venv/Scripts/pip install -r requirements.txt    # Windows
# Linux/RPi: source .venv/bin/activate && pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

原始数据默认落库到 `data/raw/ec.db`（仓库约定 11.1：原始数据不可变，只追加）。
可用环境变量 `EC_DB_PATH` 覆盖（测试用临时库）。

## Mock 驱动配置

默认使用固定随机种子的 `stable` 场景，以 10 Hz 生成可复现的原始 U/I/T；
目标 κ25 只用于模拟器反推阻抗，输出仍经统一计算链得到 G/κ(T)/κ25。
完整配置样例见 `configs/devices/mock.example.json`：

```bash
# 使用版本化配置文件
EC_MOCK_CONFIG=../configs/devices/mock.example.json

# 或只覆盖常用参数
EC_MOCK_SCENARIO=drift       # stable / noisy / drift / dropout
EC_SAMPLE_RATE_HZ=10
EC_MOCK_SEED=2026
```

调试注入接口默认关闭；仅在本地验收时显式设置 `EC_ENABLE_DEBUG_ENDPOINTS=1`。
跨域来源默认只允许 localhost、私有网段和 `.local` 主机；如需额外来源，可用逗号分隔的
`EC_CORS_ORIGINS` 或正则 `EC_CORS_ORIGIN_REGEX` 配置。

Mock 驱动模拟电极 I–V 测量链路（SRS v0.2）：由目标 κ25 反推原始电压/电流，
经 `app.measurement` / `app.calibration` 还原 G / κ(T) / κ25，与真实硬件的数据形态一致。
实时帧严格使用 V2 字段，不再输出或接受 `ec`/`temperature`/`timestamp` V1 别名；
旧记录只通过 `legacy_ec_us_cm` 历史列读取。
设备配置使用 `cell_constant_per_cm` / `excitation_voltage_v`；start API 对应参数为
`cell_constant_cm_inv` / `excitation_amplitude_v`。缺省使用带 `calibration_id` 的 Mock 配置，
显式传 `null` 可强制未校准并保留 Raw 帧。

## 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| WS | `/ws/stream` | 实时数据流（10Hz，实验 running 期间推送并异步落库） |
| POST | `/api/experiment/start` | 开始实验（可带 sample_id/sensor_path_id） |
| POST | `/api/experiment/stop` | 停止实验 |
| POST | `/api/experiment/reset` | 重置/重新开始 |
| GET | `/health` | 健康检查 |
| GET | `/api/experiments` | 历史实验列表（含帧数） |
| GET | `/api/experiments/{id}` | 实验详情（元信息 + 样品汇总） |
| GET | `/api/experiments/{id}/frames` | 原始帧分页 |
| GET | `/api/experiments/{id}/export.csv` | 原始帧 CSV 导出 |
| GET | `/api/experiments/{id}/export.json` | 完整实验 JSON 导出 |
| POST | `/api/debug/bad-frame` | 注入非法帧，验证前端容错 |
| POST | `/api/debug/close-connections` | 强制断开 WS，验证断线/重连 |
| POST | `/api/debug/burst?count=10000` | 快速推 N 帧，验证大点数负载；每次使用独立 `DEBUG-BURST-*` 溯源且不落库 |

## WS 消息格式（协议基准，V2）

```json
{ "message_type": "measurement", "schema_version": "2.0", "experiment_uid": "EXP-...",
  "seq_no": 123, "timestamp_utc": "2026-08-22T12:00:00.000Z",
  "monotonic_ms": 12300, "t_seconds": 12.35, "status": "running",
  "voltage_raw_v": 0.4, "current_raw_a": 0.0005652, "temperature_raw_c": 25.1,
  "conductance_s": 0.001413, "kappa_t_us_cm": 1414.7, "kappa_25_us_cm": 1413.0,
  "excitation_frequency_hz": 1000, "excitation_amplitude_v": 0.4,
  "range_id": "R_100R_10K", "sensor_path_id": "EC_IV_CELL_01",
  "calibration_id": "CAL_20260822_001", "cell_constant_cm_inv": 1.0,
  "calibration_valid_until_utc": null, "compensation_model": "linear",
  "alpha_per_c": 0.02, "quality_flags": ["SIMULATED"] }
```

- 数据分层：`voltage_raw_v`/`current_raw_a`/`temperature_raw_c`（Raw，不可变原始量）、
  `voltage_cal_v`/`current_cal_a`/`conductance_s`/`kappa_t_us_cm`（Calibrated）、
  `kappa_25_us_cm`（Derived，未校准时为空）、`seq_no`/`timestamp_utc`/`monotonic_ms`/
  `sensor_path_id`/`calibration_id`/`cell_constant_cm_inv`/`calibration_valid_until_utc`（Trace）、`excitation_*`/`range_id`/`compensation_model`/
  `alpha_per_c`（Configuration）、`quality_flags`（Quality）。
- `t_seconds`：实验开始后的秒数（float）——**仅作前端显示，审计唯一时间以 `timestamp_utc`/`monotonic_ms` 为准（rule 38）**
- 未校准时（无 Kcell）不伪造电导率：`kappa_t_us_cm`/`kappa_25_us_cm` 显式为 `null`，`quality_flags` 含 `UNCALIBRATED`
- `message_type` 明确区分 `measurement` / `status`；状态帧含 `status`，运行态附带 `experiment_uid`
- `status`：`idle` / `running` / `stopped` / `error`
- V2 measurement 的各层键必须完整存在；缺测值用 `null`，不能通过 V1 字段猜测或降级。

## 测试

```bash
.venv/Scripts/python -m pytest tests -q
```

当前共 **87 项**。覆盖：健康检查、控制状态机（含重复 start 拒绝）、WS 协议格式、停止后停流、坏帧注入、
SQLite append-only 约束（UPDATE/DELETE 被拒）、实验生命周期、历史/详情/CSV 导出、
Mock 场景可复现性、质量标志落库，以及慢 WebSocket 客户端隔离。

Phase 2 快速长跑冒烟：

```bash
python ../scripts/phase2_soak.py --duration 5 --sample-rate 20
# 树莓派正式验收
python ../scripts/phase2_soak.py --duration 1800 --sample-rate 10
```

脚本走完整的 REST 启停、WebSocket 接收与 SQLite 落库链路，并检查帧数量、
`seq_no` 连续性、单调时钟和 `SIMULATED` 质量标志。

## 接入真实后端的约定

真实后端只需提供**相同路径与相同消息格式**：
- `WS /ws/stream` 在实验运行期间按约定格式推送 ≥1Hz 数据
- `POST /api/experiment/start|stop|reset` 返回 `{ok, status, message?, experiment_id?}`
- 原始帧统一携带 `seq_no / timestamp_utc / monotonic_ms / sensor_path_id`（rule 38）
