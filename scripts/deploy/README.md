# scripts/deploy — 树莓派部署

将本项目部署到 Raspberry Pi（Raspberry Pi OS，桌面版）并开机自动全屏显示实验界面。

## 前置

- Raspberry Pi OS 桌面版，Python 3.11+，**Node.js 24 LTS**（安装：`curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash - && sudo apt install -y nodejs`）
- 已安装 Chromium 浏览器：`sudo apt install -y chromium`（旧版系统也兼容 `chromium-browser` 命令）
- 系统使用图形桌面；脚本会通过 `raspi-config` 设置为桌面自动登录

## 用法

把整个 `electrochem-platform` 目录放到树莓派上（建议路径 `/home/pi/electrochem-platform`），然后：

```bash
cd /home/pi/electrochem-platform
chmod +x scripts/deploy/setup.sh
./scripts/deploy/setup.sh
```

请以需要自动打开 Kiosk 的**桌面登录用户**执行，不要使用 `sudo ./scripts/deploy/setup.sh`；
脚本会在安装 systemd 服务时自行调用 `sudo`。这样可确保自启动配置写入正确用户的主目录。

脚本会自动完成：

1. 构建前端（`frontend/dist`，默认连接 `ws://localhost:8000`）
2. 创建 `backend/` 虚拟环境并安装依赖
3. 生成并安装两个 systemd 服务：
   - `ec-backend`：FastAPI 后端（仅绑定 127.0.0.1:8000，开机自启 + 崩溃自动重启）
   - `ec-web`：静态托管前端产物（仅绑定 127.0.0.1:5173）
4. 自动识别 `chromium` / `chromium-browser`，设置桌面自动登录并安装 Kiosk 自启动：
   - 新版 Raspberry Pi OS（Labwc）：`~/.config/labwc/autostart`
   - 旧版 LXDE/X11：`~/.config/autostart/ec-kiosk.desktop`

脚本可以重复执行：每次都会重新构建前端、同步后端依赖，并重启两个 systemd 服务。
重启树莓派后，会自动全屏打开 `http://localhost:5173` 的实验界面。

## 常用命令

```bash
sudo systemctl status ec-backend ec-web  # 查看服务状态
sudo systemctl restart ec-backend ec-web # 重启前后端
pgrep -a chromium                        # 检查 Kiosk 浏览器进程
cat ~/.config/labwc/autostart            # 检查新版系统的自启动配置
```

如果服务正常但重启后没有打开 Chromium，请检查：

```bash
command -v chromium || command -v chromium-browser
systemctl get-default
curl -fsS http://127.0.0.1:8000/health
curl -I http://127.0.0.1:5173/
```

## 自定义

- **前端连接地址**：`setup.sh` 构建前会检查 `frontend/.env.local`（见 `frontend/.env.example`），
  需要连接其他主机时在此配置 `VITE_WS_URL` / `VITE_API_BASE`。
- **切换真实后端**：只需改 `frontend/.env.local` 后重新 `./setup.sh`，无需改任何业务代码。

## 说明

- 真实后端就绪后，`backend/` 内部替换为真实设备驱动与采集进程即可；协议对齐见 `docs/接口说明.md`。
- 本项目为 MVP 教学演示用途，未配置鉴权与 HTTPS。服务默认只监听本机，Kiosk 通过 `http://localhost:5173` 访问；若需局域网访问，请自行加防火墙与反向代理，不要把控制面裸绑到 `0.0.0.0`。
