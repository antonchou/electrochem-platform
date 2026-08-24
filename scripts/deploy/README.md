# scripts/deploy — 树莓派部署

将本项目部署到 Raspberry Pi OS 桌面版，开机后 Chromium Kiosk 全屏打开实验界面。

**Pi 5 8GB 完全够用**：可以在设备上 `npm ci` / `npm run build`。运行时 **不需要 Node**，也不再单独监听 5173。

## 前端是怎么托管的

有两种用法，不要混在一起：

| 模式 | 谁提供页面 | 地址 | 何时用 |
|---|---|---|---|
| **生产 / Kiosk（本脚本）** | FastAPI 直接托管 `frontend/dist` | `http://localhost:8000` | 开机自启、正式实验 |
| **开发热更新** | Vite `npm run dev` | `http://localhost:5173`（页面）+ `:8000`（API/WS） | 改 React 代码时 |

`npm ci` 只装依赖，**不会**启动网站。还需要：

```bash
cd frontend && npm run build     # 生成 dist/
# 然后由后端 uvicorn 提供 dist，浏览器只访问 :8000
```

页面、REST、WebSocket 都在 **同一个 8000 端口**。`VITE_WS_URL` / `VITE_API_BASE` 留空即可（按当前主机名连 `:8000`）。

旧版脚本曾用 `python3 -m http.server 5173` 另起一个静态服务；现已去掉。本脚本会停用 `ec-web.service`。

## 前置

- Raspberry Pi OS **64-bit** 桌面版（Pi 5），Python 3.11+
- **Node.js 24 LTS**（仅构建需要）：`curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash - && sudo apt install -y nodejs`
- Chromium：`sudo apt install -y chromium`
- 图形桌面；脚本会通过 `raspi-config` 设为桌面自动登录

## 用法

```bash
cd /home/pi/electrochem-platform   # 或你的仓库路径
chmod +x scripts/deploy/setup.sh
./scripts/deploy/setup.sh          # 不要加 sudo
```

脚本会：

1. 若无 `node_modules` 则 `npm ci`，然后 `npm run build` 写出 `frontend/dist`
2. 创建 `backend/.venv` 并安装依赖
3. 安装并重启 **唯一** systemd 服务 `ec-backend`（`127.0.0.1:8000`）
4. 配置 Chromium Kiosk 打开 `http://localhost:8000`
   - Bookworm/Trixie（Labwc）：`~/.config/labwc/autostart`
   - 旧版 LXDE/X11：`~/.config/autostart/ec-kiosk.desktop`

## 常用命令

```bash
sudo systemctl status ec-backend
sudo systemctl restart ec-backend
curl -fsS http://127.0.0.1:8000/health
curl -fsS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/
pgrep -a chromium
```

开发时（热更新，不要和 Kiosk 抢 5173）：

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend && npm run dev
# 浏览器打开 http://localhost:5173
```

## 自定义

- 改 UI 后重新 `./scripts/deploy/setup.sh`（会重建 dist 并重启后端）。
- 跨主机或反向代理时再设 `frontend/.env.local` 的 `VITE_WS_URL` / `VITE_API_BASE`，然后重新构建。

## 说明

- 服务只绑本机。Kiosk 走 localhost；若要局域网访问，自行加防火墙 / 反代，不要把控制面裸绑 `0.0.0.0`。
- 未配置鉴权与 HTTPS（实验室本机 kiosk）。
