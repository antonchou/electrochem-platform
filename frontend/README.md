# frontend — 实时交互与可视化前端

React 18 + TypeScript + Vite + Apache ECharts + CSS Modules + 原生 WebSocket。

## 环境

- **Node.js 24 LTS**（仓库 `.nvmrc` = 24；`npm install` 会校验 engines）

## 技术栈

| 层 | 技术 |
|---|---|
| UI | React 18, CSS Modules |
| 构建 | Vite 8 |
| 图表 | Apache ECharts（按需引入，lttb 采样） |
| 实时通信 | 原生 WebSocket |
| 控制通道 | REST API（fetch） |

## 目录职责

```
src/
├── config/config.ts    # 集中配置：数据源模式、后端地址、曲线参数（唯一配置入口）
├── types/protocol.ts   # 协议类型 + 消息校验 + 历史实验类型（Phase 7）
├── services/           # 通信层（与 UI 解耦）
│   ├── websocketClient.ts  # 原生 WS：连接/解析/断线检测/自动重连/看门狗
│   ├── apiClient.ts        # REST 控制 + 历史查询/导出（Phase 7）
│   ├── browserMock.ts      # 纯浏览器模拟源（可选模式）
│   └── index.ts            # ExperimentBridge 工厂（server / browser 二选一）
├── hooks/              # 业务逻辑
│   ├── useConnection.ts    # 连接状态 + 错误 + 手动重连
│   ├── useExperiment.ts    # 实验状态机 + 控制 + experimentId 溯源（Phase 7）
│   └── useRealtimeData.ts  # 实时数据缓冲（ref 数组 + 增量 setState）
├── components/         # 纯 UI 组件（不感知数据来源）
│   ├── HistoryPanel.tsx    # 历史实验：列表→详情（样品/静态曲线/导出）Phase 7
│   ├── StaticChart.tsx     # 历史数据静态曲线
│   └── ...
├── pages/ExperimentPage.tsx  # 主实验页（样品输入 + 历史入口）
└── main.tsx
```

## Phase 7 功能

- **样品溯源**：开始实验前可填写「样品编号」，随 `start` 请求上传，随帧入库。
- **持久化**：所有原始帧由 backend 写入 SQLite（append-only），结果区展示后即可导出。
- **历史实验**：右上角「历史实验」打开面板 → 列表 → 详情（样品表 + 静态曲线）→ 导出 CSV/JSON。
- **导出**：结果区与历史详情均有「导出 CSV」按钮（`/api/experiments/{id}/export.csv`）。

> 历史/导出依赖后端接口，`browser` 模拟模式下不可用。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VITE_DATA_SOURCE` | `server` | `server` 连接后端；`browser` 用浏览器内置模拟源 |
| `VITE_WS_URL` | 当前页面主机的 `ws(s)://…:8000/ws/stream` | 实时流地址 |
| `VITE_API_BASE` | 当前页面主机的 `http(s)://…:8000` | REST 控制地址 |

## 命令

```bash
npm install        # 安装依赖
npm run dev        # 开发服务器（端口 5173）
npm run build      # 类型检查 + 产物构建（dist/）
npm run typecheck  # 仅类型检查
npm run preview    # 预览构建产物
```

## 实时性设计（对应 P01–P04）

- **200ms 内反映**：数据缓冲用 ref 数组，WebSocket 帧到达即写入；
  曲线组件每 200ms 节流读取一次并 `setOption`。
- **10Hz 稳定输入**：10 帧/秒的 setState 驱动增量 UI；图表更新独立于 React 渲染。
- **10000 点不卡**：曲线开启 `sampling: 'lttb'` + `animation: false`；
  数据缓冲上限 `config.chart.maxPoints`（默认 20000），超出丢最旧点。
- **调试流隔离**：`DEBUG-BURST-*` 帧可注入当前视图做负载测试，但不会被当作新实验边界，
  也不会替换真实实验的 `experiment_uid`。
- **30 分钟长跑**：缓冲有上限、定时器全部清理、监听器可退订。

## 断线检测（F08/F09）

- WebSocket `close`/`error` 事件 → 立即置 `disconnected` 并提示；
- 看门狗：连接存活但 3 秒无数据 → 提示「数据流超时」；
- 自动重连：指数退避（1s 起步，最大 5s），同时提供「手动重连」按钮。

## 切模拟 ↔ 真实

只修改 `.env.local` 中的 `VITE_DATA_SOURCE` / `VITE_WS_URL` / `VITE_API_BASE`，
核心业务逻辑（hooks/页面/组件）零改动。browser 模式在无后端时也可完整演示。
