# PR 流程（Pull Request 工作流）

> 权威规范：仓库根 `CONTRIBUTING.md`（负责人维护）。本文件是对 `CONTRIBUTING.md` 的速查版，
> 两者冲突时以 `CONTRIBUTING.md` 为准。远程：GitHub 私有仓库 `antonchou/electrochem-platform`。

## 1. 基本原则

- `main` 必须始终保持可运行状态，**禁止直接在 `main` 上开发**。
- 一个分支 / 一个 PR 只处理一个明确目标。
- 禁止提交密码、私钥、访问令牌、`.env`、设备凭据；禁止提交虚拟环境/缓存/构建产物/正式实验原始数据。
- 量程、校准、数据 Schema、通信协议、实验模板的修改必须记录版本和影响。

## 2. 分支命名

```
feat/<名称>      新增功能
fix/<名称>      缺陷修复
docs/<名称>     文档修改
test/<名称>     测试工作
refactor/<名称> 代码重构
chore/<名称>    依赖、配置和工程维护
```

示例：`feat/mock-device`、`fix/websocket-reconnect`、`docs/cm2-calibration`、`test/health-endpoint`。

## 3. 提交信息规范

格式（**类型: 描述**，不带 scope）：

```text
feat: add mock EC device
fix: handle websocket disconnect
docs: document CM2 calibration procedure
test: add health endpoint test
refactor: split analysis module
chore: update dependencies
```

允许类型：`feat:` `fix:` `docs:` `test:` `refactor:` `chore:`

## 4. 标准开发流程

```bash
# 从最新 main 开分支
git switch main
git pull --ff-only
git switch -c feat/example

# 开发完成后
git add <相关文件>
git diff --cached --check
git commit -m "feat: describe the change"
git push -u origin feat/example

# 在 GitHub 发起 PR：feat/example → main（自动带 PR 模板）
# Owner 审核通过后合并；合并后回主干
git switch main && git pull
```

## 5. Pull Request 要求

每个 PR 必须说明：

- 修改目的及关联 Issue（`Closes #<编号>`）
- 主要修改内容
- 验证命令与测试结果
- 对接口、数据、测量或硬件的影响
- 潜在风险和回退方法
- 必要的截图、日志或台架验证证据

合并条件：仓库 Owner 审核通过、所有讨论解决、测试/语法/格式检查通过、
不含密钥/虚拟环境/缓存/原始数据、代码/配置/文档一致、PR 只含一个目标。

## 6. 项目数据规则（原始数据不可变）

- `data/raw/` 不可覆盖原始数据；`data/calibrated/` 校准与温补；`data/derived/` 统计/拟合/报告。
- 原始数据不得因滤波、温补、校准或人工修订而被覆盖。
- 每帧必须保留 `seq`、`timestamp_utc`、`monotonic_ms`；正式数据携带 `sensor_path_id` + `calibration_id`。
- 大型实验数据和液体记录不直接提交 Git，走实验数据存储方案。

## 7. 测量链路红线（与前端展示强相关）

- `BA121S_LOW` 与 `CM2_WIDE` 是两条独立测量链路，**分别校准**。
- 未经重叠区传递校准，不得合并拟合或计算精确跨链路倍数。
- 不得将 BA121S 接入 CM2/ADS1256；不得用 1413 μS/cm 标准液校准 BA121S。

## 8. 最低验证要求

后端修改至少：

```bash
python -m pip check
python -m compileall backend
git diff --check
```

涉及 FastAPI 接口：`/health` 返回 200、WS 连续收数、`seq` 连续递增、必需字段完整、非法输入/断连不崩溃。
涉及前端：页面可启动、Mock 数据显示、开始/停止/断线/重连状态正确、附截图或录屏。

## 9. 禁止事项

- 禁止强制推送 `main`；禁止删除或改写他人提交历史。
- 禁止在未测试的情况下合并 PR。
- 禁止静默修改数据 Schema 或实验模板。
- 禁止使用销售宣传参数替代台架验证结果。
- 禁止因硬件已采购而提前扩大当前里程碑范围。

## 首次推送（一次性）

```bash
git remote add origin git@github.com:antonchou/electrochem-platform.git
git push -u origin main
```
