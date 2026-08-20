# electrochem-platform — 树莓派电化学设备应用平台

「树莓派在电化学设备的应用开发」项目 · 溶液导电性相对比较实验。
仓库结构遵循《仓库与工程约定》11.1；当前覆盖 **Phase 3（Web 前端）+ Phase 7（数据存储与导出）**，
后端为模拟数据源，真实设备驱动（BA121S/CM2/DS18B20）按约定在 backend 内逐步替换。

## 目录结构

```
electrochem-platform/
├── backend/          # FastAPI 后端（实时流/控制/历史导出 + 模拟数据源）
├── frontend/         # React + TypeScript + Vite + ECharts Web UI
├── firmware/         # ESP32-S3 固件（占位，后续阶段）
├── configs/          # 实验模板、设备与校准配置（版本化，rule 35/36）
├── data/
│   ├── raw/          # 不可变原始数据（SQLite，append-only，不入 Git）
│   ├── calibrated/   # 校准/温补数据（预留）
│   └── derived/      # 统计、拟合、报告（预留）
├── tests/
│   └── e2e/          # Playwright 验收测试（F01–F10 / P01–P04 / P03 可选）
├── scripts/
│   └── deploy/       # 树莓派一键部署（systemd + Chromium Kiosk）
├── docs/             # 项目交接文档、接口说明、已知问题、三状态截图
├── .nvmrc            # Node 24 LTS
├── .gitignore
└── README.md
```

## 环境要求

- **Node.js 24 LTS**（`.nvmrc` = 24；Windows 便携版可放 `C:\nodejs` 并加入 PATH）
- Python ≥ 3.11

## 快速开始（模拟数据模式，无需硬件）

**1. 启动 backend（端口 8000）**

```bash
cd backend
python3 -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows
# Linux/RPi: source .venv/bin/activate && pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**2. 启动前端（端口 5173）**

```bash
cd frontend
npm install
npm run dev            # 打开 http://localhost:5173
```

点击「开始实验」即可看到实时 EC/温度/曲线；「历史实验」可回看/导出（Phase 7）。

## 切换真实后端

改 `frontend/.env.local`（`VITE_WS_URL` / `VITE_API_BASE`，见 `frontend/.env.example`），核心代码零改动。

## 运行测试

```bash
# backend 单元/协议测试（pytest + TestClient，含 SQLite append-only 约束）
cd backend && .venv/Scripts/python -m pytest tests -q

# E2E 验收（Playwright；Windows 无自带 Chromium 时用系统浏览器：E2E_BROWSER=msedge）
cd tests/e2e && npm install && E2E_BROWSER=msedge npx playwright test
```

## 树莓派部署

```bash
cd scripts/deploy && chmod +x setup.sh && ./setup.sh
# 详见 scripts/deploy/README.md
```

## 工程规则落实（约定 11.2）

| 规则 | 落实 |
|---|---|
| 34 统一 Driver Base Class | backend 内 `stream.py`（Mock）已具独立接口，后续 BA121S/CM2/DS18B20 同接口替换 |
| 35 配置/schema 版本化 | `configs/` 带 schema_version；DB 由 `init_db()` 幂等建表（迁移走脚本，不手工改表） |
| 36 配置地址集中 | 前端 `src/config/config.ts` 唯一入口；后端 DB 路径可用 `EC_DB_PATH` 覆盖 |
| 37 机密不入 Git | 根 `.gitignore` 已排除 `.env*`/私钥/令牌 |
| 38 帧溯源 | 原始帧带 `seq_no/timestamp_utc/monotonic_ms/sensor_path_id`；前端 timestamp 仅显示 |
| 39 里程碑文档 | 每阶段交付 README/启动/测试/已知问题/验收证据（见 `docs/`） |
| 40 垂直切片 | Mock 采集→实时曲线→落盘→校准→真实 EC，逐片可独立演示 |

## 里程碑

**工程骨架阶段（Phase 0.x，负责人基线）**

- Phase -1 / M0：需求与测量规格已冻结
- Phase 0.1：系统、网络、SSH 与硬件健康检查通过
- Phase 0.2：开发环境与目录结构通过
- Phase 0.3：FastAPI 健康接口与 Mock WebSocket 通过
- Phase 0.4：仓库初始化完成（骨架已并入本仓库历史，见 `docs/environment.md`）

**功能实现阶段（本仓库开发主线）**

- **Phase 3（M1–M2）**：Web 前端 + 模拟数据源闭环 ✅（F01–F10 全过）
- **Phase 7（M3–M4 前置）**：SQLite 存储 + 历史查询 + 导出 ✅（append-only 已验证）
- **备选公式拟合**：化学公式（一阶饱和 / Arrhenius / Kohlrausch）按 X 轴语义拟合 ✅
- 后续：校准/温补、拟合报告、真实 EC 采集（垂直切片推进）
