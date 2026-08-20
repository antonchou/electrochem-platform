# scripts/deploy — 树莓派部署

将本项目部署到 Raspberry Pi（Raspberry Pi OS，桌面版）并开机自动全屏显示实验界面。

## 前置

- Raspberry Pi OS 桌面版，Python 3.11+，**Node.js 24 LTS**（安装：`curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash - && sudo apt install -y nodejs`）
- 已安装 Chromium 浏览器：`sudo apt install chromium-browser`

## 用法

把整个 `electrochem-platform` 目录放到树莓派上（建议路径 `/home/pi/electrochem-platform`），然后：

```bash
cd /home/pi/electrochem-platform/scripts/deploy
chmod +x setup.sh
./setup.sh
```

脚本会自动完成：

1. 构建前端（`frontend/dist`，默认连接 `ws://localhost:8000`）
2. 创建 `backend/` 虚拟环境并安装依赖
3. 生成并安装两个 systemd 服务：
   - `ec-backend`：FastAPI 后端（8000 端口，开机自启 + 崩溃自动重启）
   - `ec-web`：静态托管前端产物（5173 端口）
4. 安装 Chromium Kiosk 开机自启动（`~/.config/autostart/ec-kiosk.desktop`）

重启树莓派后，会自动全屏打开 `http://localhost:5173` 的实验界面。

## 常用命令

```bash
sudo systemctl status ec-backend    # 查看后端状态
sudo systemctl restart ec-web       # 重启前端
systemctl --user ...                 # kiosk 随桌面自动启动
```

## 自定义

- **前端连接地址**：`setup.sh` 构建前会检查 `frontend/.env.local`（见 `frontend/.env.example`），
  需要连接其他主机时在此配置 `VITE_WS_URL` / `VITE_API_BASE`。
- **切换真实后端**：只需改 `frontend/.env.local` 后重新 `./setup.sh`，无需改任何业务代码。

## 说明

- 真实后端就绪后，`backend/` 内部替换为真实设备驱动与采集进程即可；协议对齐见 `docs/接口说明.md`。
- 本项目为 MVP 教学演示用途，未配置鉴权与 HTTPS。
