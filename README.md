# electrochem-platform — 树莓派电化学设备应用平台

「树莓派在电化学设备的应用开发」项目 · 溶液导电性相对比较实验。
仓库结构遵循《仓库与工程约定》11.1；当前覆盖 **Phase 3（Web 前端）+ Phase 7（数据存储与导出）**，
后端为模拟数据源；真实电导率链路改为**电极电压/电流采集 → 电导计算 → 电池常数校准 → 温度补偿**，DS18B20 负责溶液温度。

> **进度声明**：本仓库的 `README.md` 与 `docs/` 为当前进度的唯一真相（Phase 3 + Phase 7 + 备选公式拟合已交付，见「里程碑」）。
> 桌面版《树莓派电化学项目_交接文档_v1.0.docx》（2026-08-19）及旧《架构图》《全流程开发路线图》为历史快照；其 Phase 进度和 BA121S/CM2 模块路线均已过时。当前硬件路线见 `docs/电导率I-V测量链路与开发路线.md`。

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

- **Node.js 24 LTS**（`.nvmrc` = 24；Windows 便携版可放 `F:\nodejs24` 并加入 PATH）
- Python ≥ 3.11

## 快速开始（模拟数据模式，无需硬件）

**1. 启动 backend（端口 8000）**

```bash
cd backend
python3 -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows（生产）
# Linux/RPi: source .venv/bin/activate && pip install -r requirements.txt
# 跑测试再装：pip install -r requirements-dev.txt
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
# backend 单元/协议测试（先装 requirements-dev.txt：pytest + TestClient）
cd backend && .venv/Scripts/python -m pytest tests -q

# E2E 验收（Playwright；Windows 无自带 Chromium 时用系统浏览器：E2E_BROWSER=msedge）
cd tests/e2e && npm install && E2E_BROWSER=msedge npx playwright test
```

## 树莓派部署

生产环境由 FastAPI 在 `http://localhost:8000` **同源托管** `frontend/dist`（页面 + API + WebSocket）。
`npm ci` 只装依赖，还要 `npm run build`（或直接跑 `scripts/deploy/setup.sh`）。
Kiosk 默认只绑本机；局域网访问：`EC_BIND=0.0.0.0 ./scripts/deploy/setup.sh`。

```bash
cd scripts/deploy && chmod +x setup.sh && ./setup.sh
# 详见 scripts/deploy/README.md
```

## 工程规则落实（约定 11.2）

| 规则 | 落实 |
|---|---|
| 34 统一 Driver Base Class | `backend/app/drivers/base.py` 定义异步接口；Mock 已实现，后续电压/电流/温度采集驱动按同一边界接入 |
| 35 配置/schema 版本化 | `configs/` 带 schema_version；DB 由 `init_db()` 幂等建表（迁移走脚本，不手工改表） |
| 36 配置地址集中 | 前端 `src/config/config.ts` 唯一入口；后端 DB 路径可用 `EC_DB_PATH` 覆盖 |
| 37 机密不入 Git | 根 `.gitignore` 已排除 `.env*`/私钥/令牌 |
| 38 帧溯源 | 原始帧带 `seq_no/timestamp_utc/monotonic_ms/sensor_path_id`；前端 timestamp 仅显示 |
| 39 里程碑文档 | 每阶段交付 README/启动/测试/已知问题/验收证据（见 `docs/`） |
| 40 垂直切片 | Mock → I/V 电气台架 → 导电池标准液校准 → 温补 → 实时曲线与落盘，逐片可独立演示 |

## 里程碑

**工程骨架阶段（Phase 0.x，负责人基线）**

- Phase -1 / M0：需求与测量规格已冻结
- Phase 0.1：系统、网络、SSH 与硬件健康检查通过
- Phase 0.2：开发环境与目录结构通过
- Phase 0.3：FastAPI 健康接口与 Mock WebSocket 通过
- Phase 0.4：仓库初始化完成（骨架已并入本仓库历史，见 `docs/environment.md`）

**功能实现阶段（本仓库开发主线）**

- **Phase 3（M1–M2）**：Web 前端 + 模拟数据源闭环 ✅（F01–F10 全过）
- **Phase 2 集成加固**：统一驱动接口、可配置 Mock 场景、慢客户端隔离、SQLite 长跑验收 ✅
- **Phase 7（M3–M4 前置）**：SQLite 存储 + 历史查询 + 导出 ✅（append-only 已验证）
- **备选公式拟合**：化学公式（一阶饱和 / Arrhenius / Kohlrausch）按 X 轴语义拟合 ✅
- 后续：电压/电流与温度真实采集、`Kcell` 校准、温补、判稳与拟合报告（垂直切片推进）

## 已知缺口与交付阶段（冻结 SRS 未实现项）

以下为**冻结基线 SRS 中当前未实现**的强制需求（评审 R-1~R-6），均属后续交付范围，**勿误判为已满足全部 REQ**。
每项标注交付阶段，随垂直切片（rule 40）逐步补齐。

| 需求编号 | 需求内容 | 现状 | 交付阶段 |
|---|---|---|---|
| REQ-M-001 | 帧需保存 U、I、T，并可追溯计算 G、κ(T)、κ25 | Mock/CSV 已走 I–V 计算链并落库；真实电极采集未接 | 后续阶段（真实 I/V 采集、Kcell 校准） |
| REQ-F-001 / REQ-F-002 | 拟合需输出 CI/残差/RMSE/MAE/AICc/留一交叉验证，声明有效浓度区间、禁止外推 | 已输出 MAE/AICc/残差峰值/Wald CI/LOOCV 与有效区间；结果写入 fit_results 与 data/derived | 后续：PDF 报表 |
| REQ-D-003 | 自动判稳（窗口/统计量/阈值/失败原因）与 QC PASS/WARN/FAIL | 实验停止时已写入 samples.qc_*；结果区显示 PASS/WARN/FAIL 与代表值 | 后续：阈值台架标定 |
| REQ-C-001 | 每次结果关联 calibration_id 与标准液批次 | 开始实验写入 mock 校准记录；每帧落库 `calibration_id` 与激励/协议元数据 | 后续阶段（真实标准液 SOP） |
| REQ-U-001 | UI 区分原始值/温补值/滤波值/最终代表值 | 已显示 U/I/G/κ(T)/κ25；滤波值尚未分层 | 后续阶段（滤波层） |
| SRS 3.3 | 协议需含 schema_version/device_id/firmware_version/range_id/quality_flags | v2 帧已含上述字段；COMPUTE_INVALID 时 `ec` 可为 null | 后续阶段（真实设备接入） |
