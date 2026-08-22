# 代码审查报告：chemisty（修复后项目）

> **历史快照，已失效**：本文审查的是 2026-08-22 当时的 V1 回退工作树，不能描述当前代码。
> 当前工作树已同步到 `main` @ `e0429ab` 的严格 V2 I–V 基线；例如 V2 measurement、
> DROPOUT 质量帧、版本化 SQLite 迁移和 86 项基线测试均已恢复。本文以下内容仅供追溯，
> 不应再作为待修复清单或当前交付说明；当前状态以 README、`docs/接口说明.md`、
> `docs/已知问题.md` 及实际测试结果为准。

- **审查日期**：2026-08-22
- **审查对象**：`D:\project`（后端 FastAPI + 前端 React/Vite + Playwright）
- **对比基线**：主仓库 `electrochem-platform`（e8421fc，PR #4 iv-measurement-v2）及上一轮 review（2026-08-22 上午）发现的 15 个问题
- **审查方法**：全量代码走读（后端 14 个 py + 前端 16 个 ts/tsx + 测试）+ 后端测试运行（49 passed）+ 数据库/配置兼容性核查
- **结论**：chemisty 是**协议回退版**（V2 电极 I–V 链路 → V1 仅 EC/温度），以「删繁就简」方式规避了上一轮多数 V2 特有缺陷，并重写了实时曲线组件（修复 F04b 测试）。但**回退同时放弃了已交付的 V2 能力**，且 4 个遗留问题未修复、另有若干新问题。**未修改任何代码。**

---

## 一、验证结果

| 项目 | 结果 |
|---|---|
| 后端 pytest（7 个测试文件） | **49 passed**（29.8s；主仓库为 67 个，`test_calibration.py`/`test_measurement.py` 已随 V1 回退删除） |
| `data/raw/ec.db` schema 核查 | V1 表结构（`ec_raw`/`temperature_raw`），与代码匹配；68 实验 / 562 帧 |
| `configs/devices/mock.example.json` | V1.0.0 字段，与 V1 `MockDeviceConfig.from_mapping` 匹配 |
| 协议一致性核查 | 后端帧 `{timestamp,ec,temperature,status}` = 前端 `parseServerMessage` 校验集合 ✓ |
| e2e F04b 坐标断言 | `data-chart-x-max/chart-y-min/chart-y-max` 属性已在 RealTimeChart 实现（`el.dataset.*`）✓ |

---

## 二、上一轮 15 个问题的处置矩阵

| # | 上轮问题 | 处置 | 说明 |
|---|---|---|---|
| H1 | 未校准实验前端无数据（`'ec' in parsed`） | ✅ 规避 | V1 协议帧必有 ec，无未校准概念 |
| M1 | MockDevice Kcell=0 除零崩溃 | ✅ 规避 | V1 mock 无电阻计算（无 U/I） |
| M2 | κ25 温补负分母 | ✅ 规避 | V1 无 κ25/温补计算 |
| M3 | e2e F04b 引用不存在的 DOM 属性 | ✅ **已修复** | RealTimeChart 重写，实现 `dataset.chartXMax/chartYMin/chartYMax` 且坐标轴单调扩展 |
| L1 | reset 与 (stop→start) 并发清掉新实验上下文 | ❌ **未修复** | `routes.reset` 仍不持 `_start_lock`，`state.reset()` 无条件清空（`routes.py:250-260`） |
| L2 | start 缺校准参数校验 | ✅ 规避 | V1 无校准参数 |
| L3 | ValueDisplay 显示 NaN | ✅ 规避 | V1 `ec`/`temperature` 经 `parseServerMessage` 校验必有且有效 |
| L4 | HistoryPanel 缺字段用 0 兜底 | ✅ 规避 | V1 `ec_raw`/`temperature_raw` 为 NOT NULL 列 |
| L5 | insert_frames 旧字段 fallback 失效 | ✅ 规避 | V1 单字段路径 |
| L6 | CSV 表头与列名不一致 | ❌ **未修复** | 表头 `ec_raw_us_cm`/`temperature_raw_c`/`k25_us_cm`，数据列 `ec_raw`/`temperature_raw`/`k25`（`storage.py:398-416`） |
| L7 | browserMock connect 双 timer | ❌ **未修复** | `connect()` 未清旧 `connectDelay`（`browserMock.ts:26`）；主仓库工作区已修，chemisty 未同步 |
| L8 | burst 帧缺协议字段 | ✅ 规避 | V1 协议本就只需 4 字段 |
| L9 | storage `with _conn()` 不显式关闭 | ❌ 仍存在（低） | 依赖 CPython 引用计数兜底 |
| L10 | upsert_sample 冲突不更新浓度 | ❌ 仍存在（低） | ON CONFLICT 只更新 frame_count/k25 |
| L11 | DROPOUT 帧不广播、质量标志不可见 | ❌ **未修复（更隐蔽）** | V1 广播帧**根本没有 `quality_flags` 字段**，OUT_OF_RANGE/DROPOUT 对前端完全不可见；主仓库工作区已新增 `test_dropout_is_persisted_and_broadcast_as_quality_frame`，chemisty 无对应实现 |

**小结**：15 项中 7 项规避、1 项真正修复（M3）、4 项未修复（L1/L6/L7/L11）、2 项低危遗留（L9/L10）。

---

## 三、新发现问题

### 🟠 N1（中）V2 能力整体回退，且文档与代码不一致

- `routes.py` 广播帧退化为 `{timestamp, ec, temperature, status}`；驱动层退回 `ec/temperature/ph`；`storage` 退回 V1 表；`state` 移除校准/激励上下文；前端移除 U/I/G 诊断区、κ25 显示、校准状态、质量标志展示。
- **文档未同步**：`README.md` 与 `docs/电导率I-V测量链路与开发路线.md` 仍宣称「电极电压/电流采集 → 电导计算 → 电池常数校准 → 温度补偿」路线，`docs/已知问题.md` 却写「图表只画 EC-时间单序列」——README 与代码自相矛盾。若 V2 是已交付成果，回退属于功能丢失；若 V1 是刻意收敛，文档必须更新。
- 建议：二选一明确路线，并同步 README / 接口说明 / 已知问题。

### 🟠 N2（中）实时 WS 帧不含质量标志（L11 的 V1 形态）

- `_acquisition_loop` 对 `complete_for_conductivity=False`（DROPOUT）的读数直接 `continue`，不广播任何帧（`routes.py:92-98`）；
- 正常帧也只含 4 字段，`quality_flags` 仅落库（`_frame_to_row`）、**从不广播** → 前端无法提示 OUT_OF_RANGE / 丢帧，与「Quality 层」协议语义脱节。

### 🟡 N3（中低）useExperiment 在 start 被拒时仍刷新 startedAt

- `useExperiment.ts:46`：`if (action === 'start') { setStartedAt(new Date()) ... }` 无条件执行；主仓库版已改为 `if (action === 'start' && res.ok)`。start 返回 `ok=false`（如重复开始）时仍会重置 startedAt。

### 🟡 N4（低）backend/app 子目录仅存 __pycache__，源码缺失

- `adapters/ api/ db/ domain/ export/ runtime/ services/` 七个目录**只有 .pyc 无 .py**（如 `adapters/modbus_rtu`、`domain/calibration`、`db/repositories`、`services/configuration` 等，均为 2026-08-20 编译产物）。
- 当前未被任何模块 import（`app/__init__.py` 为空壳），不影响运行，但**暗示曾有一次模块化重构/硬件适配开发，源码已丢失**，无法维护、无法演进。若这些是废弃探索，建议删除目录；若是待接入的硬件层，需找回源码。

### 🟡 N5（低）V2 旧库无迁移保护

- chemisty `storage.py` 无版本化迁移（主仓库 V2 版有 `SCHEMA_VERSION`/`MIGRATIONS`）。若误将主仓库 V2 的 `ec.db` 拷入，`insert_frames` 会因缺 `temperature_raw` 列直接报错且无提示。

### 🟡 N6（低）`ec_raw REAL NOT NULL` 无运行时兜底

- 若未来 dropout 帧也尝试落库（如接入 N2 修复时直接 `enqueue_frame` 空值帧），会触发 `IntegrityError`。当前 dropout 帧被上游跳过，恰好未触发。

### 🟡 N7（低）`insert_frames` 对帧字典键的隐式依赖

- `storage.insert_frames` 以 `:experiment_id` 等命名参数直接展开帧字典，缺键即 `KeyError`。当前唯一调用方 `_frame_to_row` 提供全键，但函数签名不约束，未来新增调用方易踩坑（建议显式白名单取值）。

---

## 四、值得肯定的修复（相对上一轮）

1. **RealTimeChart 重写**（`RealTimeChart.tsx`）：
   - 增量 `appendData` + 坐标轴显式同步，避免全量重绘，P04 万点更稳；
   - 坐标轴「只扩展不收缩」+ `niceStep` 刻度，消除读数抖动（F04b 目标）；
   - `dataset.chartXMax/chartYMin/chartYMax` 诊断属性补齐，修复 M3；
   - 缓冲数组替换检测（start/reset 重建坐标范围）。
2. **StaticChart / FitPanel / HistoryPanel UI 修复**（对应 `docs/UI修复记录-2026-08-22.md`）：图例颜色与线条一致、ResizeObserver 防模糊、Hooks 顺序修复（防潜伏崩溃）、拟合竞态防护与旧结果失效提示。
3. **browserMock / apiClient / protocol.ts 与 V1 后端严格对齐**，`parseServerMessage` 三字段齐全才认测量帧，坏帧丢弃并提示（F10 语义保留）。
4. e2e 新增 `screenshots.spec.ts`（三状态截图交付物）与 `stability.spec.ts`（P03 长跑，默认跳过需显式 `STABILITY_MINUTES`，设计合理）。
5. 后端 49 测试全部通过；`data/raw/ec.db` 与代码 schema 一致，历史数据可正常读取。

---

## 五、修复建议（按优先级）

1. **明确路线并统一文档**（N1）：V1 收敛 or V2 恢复，二者择一，同步 README/接口说明/已知问题；若保留 V2，应将主仓库工作区中未提交的 H1（`message_type` 判定）与 L11（dropout 质量帧广播）修复合并进来。
2. **L11/N2**：向 WS 帧附加 `quality_flags`（或 dropout 时广播带标志的帧），让质量状态对前端可见。
3. **L1**：reset/stop/start 共用同一把状态锁，或 `state.reset()` 仅在上下文未变时执行。
4. **L7/N3**：`connect()` 前清理旧 `connectDelay`；start 响应仅在 `ok` 时刷新 `startedAt`。
5. **L6**：统一 CSV 表头与数据列命名；**N4**：删除或找回只有 pyc 的 7 个子目录。
6. 低危项（L9/L10/N5/N6/N7）随重构顺手处理。

---

## 附：审查说明

- 本次审查**未修改任何代码**（含主仓库与 chemisty）。
- 主仓库工作区存在**未提交修改**（`calibration.py`/`routes.py`/`websocketClient.ts`/`protocol.ts` 等，含 H1 `message_type` 修复与 dropout 质量帧测试）——若主仓库是最终交付线，请先提交这些改动，避免修复丢失；chemisty 的 review 结论不受其影响。
