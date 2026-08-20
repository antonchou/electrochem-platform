# backend — FastAPI 后端（模拟数据源 → 真实设备驱动）

提供实时数据流、实验控制、历史查询与导出（Phase 7）。当前为模拟数据源；
**真实设备接入后，前端仅需改连接地址，核心代码不变**（驱动按统一 Driver Base Class 替换，rule 34）。

## 目录

```
backend/
├── app/
│   ├── main.py          # FastAPI 入口（lifespan 启停持久化）
│   ├── routes.py        # WS 实时流 + REST 控制 + 历史/导出
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
.venv/Scripts/python -m pytest tests -q
```

覆盖：健康检查、控制状态机（含重复 start 拒绝）、WS 协议格式、停止后停流、坏帧注入、
SQLite append-only 约束（UPDATE/DELETE 被拒）、实验生命周期、历史/详情/CSV 导出。

## 接入真实后端的约定

真实后端只需提供**相同路径与相同消息格式**：
- `WS /ws/stream` 在实验运行期间按约定格式推送 ≥1Hz 数据
- `POST /api/experiment/start|stop|reset` 返回 `{ok, status, message?, experiment_id?}`
- 原始帧统一携带 `seq_no / timestamp_utc / monotonic_ms / sensor_path_id`（rule 38）
