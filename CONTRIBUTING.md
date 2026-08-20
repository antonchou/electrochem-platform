# 项目协作规范

## 1. 基本原则

- `main`分支必须始终保持可运行状态。
- 禁止直接在`main`分支进行日常开发。
- 每个分支和Pull Request只处理一个明确目标。
- 禁止提交密码、私钥、访问令牌、`.env`和设备凭据。
- 禁止将虚拟环境、缓存、构建产物和正式实验原始数据提交到Git。
- 量程、校准、数据Schema、通信协议和实验模板的修改必须记录版本和影响。

## 2. 分支命名

使用以下格式：

- `feat/<名称>`：新增功能
- `fix/<名称>`：缺陷修复
- `docs/<名称>`：文档修改
- `test/<名称>`：测试工作
- `refactor/<名称>`：代码重构
- `chore/<名称>`：依赖、配置和工程维护

示例：

```text
feat/mock-device
fix/websocket-reconnect
docs/cm2-calibration
test/health-endpoint
```

## 3. 标准开发流程

从最新`main`创建工作分支：

```bash
git switch main
git pull --ff-only
git switch -c feat/example
```

完成修改后：

```bash
git add <相关文件>
git diff --cached --check
git commit -m "feat: describe the change"
git push -u origin feat/example
```

随后在GitHub创建Pull Request，通过审核后合并。

## 4. 提交信息规范

格式：

```text
类型: 简明描述
```

允许的类型：

- `feat:` 新功能
- `fix:` 缺陷修复
- `docs:` 文档修改
- `test:` 测试
- `refactor:` 重构
- `chore:` 工程维护

示例：

```text
feat: add mock EC device
fix: handle websocket disconnect
docs: document CM2 calibration procedure
test: add health endpoint test
```

## 5. Pull Request要求

每个Pull Request必须说明：

- 修改目的及关联Issue
- 主要修改内容
- 验证命令与测试结果
- 对接口、数据、测量或硬件的影响
- 潜在风险和回退方法
- 必要的截图、日志或台架验证证据

合并条件：

- 仓库Owner审核通过
- 所有审查讨论均已解决
- 测试、语法检查和格式检查通过
- 不包含密钥、虚拟环境、缓存或原始实验数据
- 代码、配置和文档保持一致
- Pull Request只包含一个明确目标

## 6. 项目数据规则

- `data/raw/`保存不可覆盖的原始数据。
- `data/calibrated/`保存校准和温度补偿结果。
- `data/derived/`保存统计、拟合和报告结果。
- Raw / Calibrated / Derived必须分层。
- 原始数据不得因滤波、温补、校准或人工修订而被覆盖。
- 每帧数据必须保留`seq`、`timestamp_utc`和`monotonic_ms`。
- 正式数据必须携带`sensor_path_id`和`calibration_id`。
- 大型实验数据和液体记录不直接提交Git，应通过实验数据存储方案管理。

## 7. 测量链路规则

- `BA121S_LOW`与`CM2_WIDE`是两条独立测量链路。
- 两条链路必须分别校准。
- 未经重叠区传递校准，不得合并拟合或计算精确跨链路倍数。
- 不得将BA121S接入CM2或ADS1256。
- 不得使用1413 μS/cm标准液校准BA121S。
- 量程、精度、采样率或校准方案变化必须更新文档版本。
- 硬件修改必须附接线说明、安全边界和台架验证结果。

## 8. 最低验证要求

后端修改至少运行：

```bash
source .venv/bin/activate
python -m pip check
python -m compileall backend
git diff --check
```

涉及FastAPI接口时还应验证：

- `GET /health`返回HTTP 200
- WebSocket能够连续接收数据
- `seq`连续递增
- 必需字段完整
- 非法输入或连接断开不会导致服务崩溃

涉及前端时还应验证：

- 页面可以正常启动
- Mock数据可以显示
- 开始、停止、断线和重连状态正确
- 提供必要的截图或录屏

涉及硬件时还应验证：

- 接线、电压和逻辑电平正确
- 没有短路、地环路或多探头激励干扰
- 附带台架数据、环境条件和校准记录

## 9. 禁止事项

- 禁止强制推送到`main`
- 禁止删除或改写他人的提交历史
- 禁止在未测试的情况下合并PR
- 禁止提交真实密码、令牌和私钥
- 禁止静默修改数据Schema或实验模板
- 禁止使用销售宣传参数替代台架验证结果
- 禁止因为硬件已经采购就提前扩大当前里程碑范围
