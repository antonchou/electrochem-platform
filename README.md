# Raspberry Pi Electrochem Platform

树莓派电化学实验数据采集与分析平台。

## 当前状态

- Phase -1 / M0：需求与测量规格已冻结
- Phase 0.1：系统、网络、SSH与硬件健康检查通过
- Phase 0.2：开发环境与目录结构通过
- Phase 0.3：FastAPI健康接口与Mock WebSocket通过
- Phase 0.4：仓库初始化进行中

## 当前接口

- `GET /health`：后端健康检查
- `GET /docs`：FastAPI接口文档
- `WS /ws/mock`：模拟EC与温度数据流

## 启动后端

```bash
source .venv/bin/activate
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
