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
├── requirements.txt       # 生产
├── requirements-dev.txt   # 生产 + pytest/httpx
└── README.md
```

## 启动

```bash
python3 -m venv .venv
.venv/Scripts/pip install -r requirements.txt    # Windows（生产）
# Linux/RPi: source .venv/bin/activate && pip install -r requirements.txt
# 开发/测试：pip install -r requirements-dev.txt
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 局域网：EC_BIND=0.0.0.0 ../scripts/run_backend.sh
```

原始数据默认落库到 `data/raw/ec.db`（仓库约定 11.1：原始数据不可变，只追加）。
可用环境变量 `EC_DB_PATH` 覆盖（测试用临时库）。

## 采集驱动（EC_DRIVER）

业务层只认 `DeviceDriver.read() → DriverReading`。换数据源只改环境变量，不改 WebSocket / 计算链 / 前端。

| `EC_DRIVER` | 适配器 | 何时用 |
|---|---|---|
| `mock`（缺省） | `MockDevice` | 生产 kiosk、原有联调 |
| `simulator` | `SimulatorDriver` | 无硬件时做电压扫描与 I–V 算法验收 |
| `csv` | `CsvPlaybackDriver` | 回放 4 列历史 CSV |

未来 ADS1256 实现同一个 `DeviceDriver`，在 `_build_driver()` 再加一个分支即可。

## Mock 驱动配置

默认使用固定随机种子的 `stable` 场景，以 10 Hz 生成可复现的 EC/温度数据。
完整配置样例见 `configs/devices/mock.example.json`：

```bash
# 使用版本化配置文件
EC_MOCK_CONFIG=../configs/devices/mock.example.json

# 或只覆盖常用参数
EC_MOCK_SCENARIO=drift       # stable / noisy / drift / dropout
EC_SAMPLE_RATE_HZ=10
EC_MOCK_SEED=2026
```

## Simulator（实验模拟器）

默认**不影响** mock 生产行为。输出原始 V/I/T（带 `SIMULATED`），G/κ 仍由 `measurement.compute_chain` 计算。

```bash
# 仓库根目录
chmod +x scripts/run_simulator.sh
./scripts/run_simulator.sh
# 前端：开发用 npm run dev（:5173），或打开 http://127.0.0.1:8000
```

| 变量 | 说明 |
|---|---|
| `EC_DRIVER=simulator` | 启用模拟器 |
| `EC_SIM_CONFIG` | JSON，见 `configs/devices/simulator*.json` |
| `EC_SIM_MODE` | `stable` / `realistic` / `fault` |
| `EC_SIM_G` | 标称电导 G（S），用于 I = G·V |
| `EC_CSV_SPEED` | CSV 回放倍速（1 / 2 / 10）；暂停 = 实验 stop |

- **stable**：低噪声、线性扫描 0.2→1.0 V
- **realistic**：ADC 噪声、偏置、温漂、弱非线性、极化滞后
- **fault**：`fault_kind`（dropout / disconnected / voltage_oor / current_zero / adc_sat / temp_abnormal / unstable）

## CSV 回放

`EC_DRIVER=csv EC_CSV_PATH=../data/fixtures/ingest/braun_2022_lib_cell.csv EC_CSV_SPEED=1`

调试注入接口默认关闭；仅在本地验收时显式设置 `EC_ENABLE_DEBUG_ENDPOINTS=1`。
跨域来源默认只允许 localhost、私有网段和 `.local` 主机；如需额外来源，可用逗号分隔的
`EC_CORS_ORIGINS` 或正则 `EC_CORS_ORIGIN_REGEX` 配置。

Mock 驱动内部同时预留 pH 原始读数，但当前电导率实验的 WS/SQLite 协议仍只发布
EC 与温度，避免破坏已交付前端。真实设备后续实现同一个 `DeviceDriver` 接口。

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
| POST | `/api/debug/burst?count=10000` | 快速推 N 帧，验证大点数负载 |

## WS 消息格式（协议基准）

```json
{ "timestamp": 12.35, "ec": 1412.8, "temperature": 25.3, "status": "running" }
```

- `timestamp`：实验开始后的秒数（float）——**仅作前端显示，审计唯一时间以 `timestamp_utc`/`monotonic_ms` 为准（rule 38）**
- `ec`：电导率，μS/cm；`temperature`：温度，°C
- `status`：`idle` / `running` / `stopped` / `error`

状态广播帧（如停止/重置后）可能只含 `status` 字段。

## 测试

```bash
pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest tests -q
```

覆盖：健康检查、控制状态机（含重复 start 拒绝）、WS 协议格式、停止后停流、坏帧注入、
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
