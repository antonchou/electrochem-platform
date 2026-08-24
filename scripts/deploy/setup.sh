#!/usr/bin/env bash
# 树莓派一键部署：构建前端 + 安装依赖 + 安装 systemd 服务 + Kiosk 自启动
# 用法：以桌面登录用户执行 ./scripts/deploy/setup.sh（不要在脚本前加 sudo）
#
# 前端由 FastAPI 在 :8000 同源托管 frontend/dist，不再单独起 :5173。
set -euo pipefail

if [ "${EUID}" -eq 0 ]; then
  echo "错误：请以树莓派桌面登录用户运行本脚本，不要使用 sudo ./setup.sh。"
  echo "      脚本会在安装 systemd 服务时自行调用 sudo。"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
DEPLOY_USER="$(id -un)"
DEPLOY_HOME="${HOME}"
echo "==> 项目目录: $PROJECT_DIR"
echo "==> 部署用户: $DEPLOY_USER"

# ---------- 1. 前端依赖与构建 ----------
echo "[1/3] 构建前端到 frontend/dist（由后端 :8000 托管）"
cd "$PROJECT_DIR/frontend"
if [ ! -d node_modules ]; then
  if [ -f package-lock.json ]; then
    npm ci
  else
    npm install
  fi
else
  echo "    已有 node_modules，跳过 npm ci"
fi
npm run build
if [ ! -f "$PROJECT_DIR/frontend/dist/index.html" ]; then
  echo "错误：frontend/dist/index.html 未生成"
  exit 1
fi

# ---------- 2. backend 依赖 ----------
echo "[2/3] 安装 backend 依赖"
cd "$PROJECT_DIR/backend"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
if [ -f requirements.lock.txt ]; then
  .venv/bin/pip install -r requirements.lock.txt
else
  .venv/bin/pip install -r requirements.txt
fi

# ---------- 3. systemd：只保留 ec-backend ----------
echo "[3/3] 安装 systemd 服务（页面 + API + WebSocket 都在 :8000）"
UNIT_DIR="$(mktemp -d)"
SVC_BACKEND="${UNIT_DIR}/ec-backend.service"

cat > "$SVC_BACKEND" <<EOF
[Unit]
Description=EC Experiment (FastAPI + frontend)
After=network.target

[Service]
User=${DEPLOY_USER}
WorkingDirectory=${PROJECT_DIR}/backend
ExecStart=${PROJECT_DIR}/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

sudo install -m 0644 "$SVC_BACKEND" /etc/systemd/system/ec-backend.service
rm -rf "$UNIT_DIR"

# 旧版用 python http.server 在 :5173 托管 dist，升级后停掉以免和同源托管打架。
if systemctl list-unit-files ec-web.service >/dev/null 2>&1; then
  sudo systemctl disable --now ec-web.service >/dev/null 2>&1 || true
  sudo rm -f /etc/systemd/system/ec-web.service
fi

sudo systemctl daemon-reload
sudo systemctl enable ec-backend
sudo systemctl restart ec-backend
echo "    服务已启用并重启：ec-backend → http://127.0.0.1:8000"

# ---------- Chromium Kiosk 自启动 ----------
echo "==> 配置 Chromium Kiosk 自启动"
CHROMIUM_BIN="$(command -v chromium || command -v chromium-browser || true)"
if [ -z "$CHROMIUM_BIN" ]; then
  echo "错误：未找到 Chromium。请先执行：sudo apt install -y chromium"
  exit 1
fi

if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_boot_behaviour B4
else
  echo "警告：未找到 raspi-config，请手动确认系统会启动到桌面并自动登录。"
fi

KIOSK_COMMON="--kiosk --noerrdialogs --disable-infobars --no-first-run --disable-session-crashed-bubble --disable-dev-shm-usage --check-for-update-interval=31536000 --enable-gpu-rasterization"
KIOSK_URL="http://localhost:8000"

if command -v labwc >/dev/null 2>&1; then
  KIOSK_ARGS="${KIOSK_COMMON} --ozone-platform=wayland"
  LABWC_AUTOSTART="${DEPLOY_HOME}/.config/labwc/autostart"
  mkdir -p "$(dirname "$LABWC_AUTOSTART")"
  touch "$LABWC_AUTOSTART"

  sed -i '/^# BEGIN EC EXPERIMENT KIOSK$/,/^# END EC EXPERIMENT KIOSK$/d' "$LABWC_AUTOSTART"
  sed -i '/chromium.*http:\/\/localhost:5173/d' "$LABWC_AUTOSTART"
  sed -i '/chromium.*http:\/\/localhost:8000/d' "$LABWC_AUTOSTART"
  cat >> "$LABWC_AUTOSTART" <<EOF

# BEGIN EC EXPERIMENT KIOSK
sleep 5 && ${CHROMIUM_BIN} ${KIOSK_ARGS} ${KIOSK_URL} &
# END EC EXPERIMENT KIOSK
EOF

  LEGACY_DESKTOP="${DEPLOY_HOME}/.config/autostart/ec-kiosk.desktop"
  if [ -f "$LEGACY_DESKTOP" ] && grep -Fq "Name=EC Experiment Kiosk" "$LEGACY_DESKTOP"; then
    rm -f "$LEGACY_DESKTOP"
  fi
  echo "    已写入 Labwc 自启动：$LABWC_AUTOSTART"
else
  KIOSK_ARGS="${KIOSK_COMMON} --start-maximized"
  XDG_AUTOSTART_DIR="${DEPLOY_HOME}/.config/autostart"
  mkdir -p "$XDG_AUTOSTART_DIR"
  cat > "${XDG_AUTOSTART_DIR}/ec-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=EC Experiment Kiosk
Comment=Open EC experiment UI in kiosk mode on login
Exec=${CHROMIUM_BIN} ${KIOSK_ARGS} ${KIOSK_URL}
X-GNOME-Autostart-enabled=true
EOF
  echo "    已写入 XDG 自启动：${XDG_AUTOSTART_DIR}/ec-kiosk.desktop"
fi

echo "==> 部署完成。重启树莓派后将自动全屏打开 ${KIOSK_URL}"
echo "    开发联调仍可用：cd frontend && npm run dev  （Vite :5173，API 仍连 :8000）"
