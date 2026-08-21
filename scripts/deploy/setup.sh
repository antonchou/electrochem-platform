#!/usr/bin/env bash
# 树莓派一键部署：构建前端 + 安装依赖 + 安装 systemd 服务 + Kiosk 自启动
# 用法：在 scripts/deploy/ 目录执行 ./setup.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
echo "==> 项目目录: $PROJECT_DIR"

# ---------- 1. 前端依赖与构建 ----------
echo "[1/4] 构建前端"
cd "$PROJECT_DIR/frontend"
if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi
npm run build

# ---------- 2. backend 依赖 ----------
echo "[2/4] 安装 backend 依赖"
cd "$PROJECT_DIR/backend"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -r requirements.txt

# ---------- 3. systemd 服务 ----------
echo "[3/4] 安装 systemd 服务"
SVC_BACKEND="/tmp/ec-backend.service"
SVC_WEB="/tmp/ec-web.service"

cat > "$SVC_BACKEND" <<EOF
[Unit]
Description=EC Experiment Backend (FastAPI)
After=network.target

[Service]
User=${USER}
WorkingDirectory=${PROJECT_DIR}/backend
ExecStart=${PROJECT_DIR}/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > "$SVC_WEB" <<EOF
[Unit]
Description=EC Experiment Web Frontend (static dist)
After=network.target ec-backend.service
Requires=ec-backend.service

[Service]
User=${USER}
WorkingDirectory=${PROJECT_DIR}/frontend
ExecStart=/usr/bin/python3 -m http.server 5173 --bind 0.0.0.0 --directory ${PROJECT_DIR}/frontend/dist
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo cp "$SVC_BACKEND" /etc/systemd/system/ec-backend.service
sudo cp "$SVC_WEB" /etc/systemd/system/ec-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now ec-backend ec-web
echo "    服务已启用：ec-backend(8000) / ec-web(5173)"

# ---------- 4. Chromium Kiosk 自启动 ----------
echo "[4/4] 配置 Chromium Kiosk 自启动"
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/ec-kiosk.desktop <<EOF
[Desktop Entry]
Type=Application
Name=EC Experiment Kiosk
Comment=Open EC experiment UI in kiosk mode on login
Exec=chromium-browser --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble --check-for-update-interval=31536000 http://localhost:5173
X-GNOME-Autostart-enabled=true
EOF
echo "    已写入 ~/.config/autostart/ec-kiosk.desktop"

echo "==> 部署完成。重启树莓派后将自动全屏打开 http://localhost:5173"
echo "    如需连接真实后端，编辑 frontend/.env.local 后重新执行本脚本。"
