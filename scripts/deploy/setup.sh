#!/usr/bin/env bash
# 树莓派一键部署：构建前端 + 安装依赖 + 安装 systemd 服务 + Kiosk 自启动
# 用法：以桌面登录用户执行 ./scripts/deploy/setup.sh（不要在脚本前加 sudo）
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
if [ -f requirements.lock.txt ]; then
  .venv/bin/pip install -r requirements.lock.txt
else
  .venv/bin/pip install -r requirements.txt
fi

# ---------- 3. systemd 服务 ----------
echo "[3/4] 安装 systemd 服务"
UNIT_DIR="$(mktemp -d)"
SVC_BACKEND="${UNIT_DIR}/ec-backend.service"
SVC_WEB="${UNIT_DIR}/ec-web.service"

cat > "$SVC_BACKEND" <<EOF
[Unit]
Description=EC Experiment Backend (FastAPI)
After=network.target

[Service]
User=${DEPLOY_USER}
WorkingDirectory=${PROJECT_DIR}/backend
ExecStart="${PROJECT_DIR}/backend/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

cat > "$SVC_WEB" <<EOF
[Unit]
Description=EC Experiment Web Frontend (static dist)
After=network.target ec-backend.service
Requires=ec-backend.service

[Service]
User=${DEPLOY_USER}
WorkingDirectory=${PROJECT_DIR}/frontend
ExecStart=/usr/bin/python3 -m http.server 5173 --bind 127.0.0.1 --directory "${PROJECT_DIR}/frontend/dist"
Restart=always
RestartSec=3
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

sudo install -m 0644 "$SVC_BACKEND" /etc/systemd/system/ec-backend.service
sudo install -m 0644 "$SVC_WEB" /etc/systemd/system/ec-web.service
rm -rf "$UNIT_DIR"
sudo systemctl daemon-reload
sudo systemctl enable ec-backend ec-web
sudo systemctl restart ec-backend
sudo systemctl restart ec-web
echo "    服务已启用并重启：ec-backend(8000) / ec-web(5173)"

# ---------- 4. Chromium Kiosk 自启动 ----------
echo "[4/4] 配置 Chromium Kiosk 自启动"
CHROMIUM_BIN="$(command -v chromium || command -v chromium-browser || true)"
if [ -z "$CHROMIUM_BIN" ]; then
  echo "错误：未找到 Chromium。请先执行：sudo apt install -y chromium"
  exit 1
fi

# Kiosk 依赖图形桌面自动登录；B4 = desktop autologin。
if command -v raspi-config >/dev/null 2>&1; then
  sudo raspi-config nonint do_boot_behaviour B4
else
  echo "警告：未找到 raspi-config，请手动确认系统会启动到桌面并自动登录。"
fi

KIOSK_ARGS="--kiosk --noerrdialogs --disable-infobars --no-first-run --disable-session-crashed-bubble --check-for-update-interval=31536000 --start-maximized"

if command -v labwc >/dev/null 2>&1; then
  # Raspberry Pi OS Bookworm/Trixie 使用 Labwc；官方推荐此自启动入口。
  LABWC_AUTOSTART="${DEPLOY_HOME}/.config/labwc/autostart"
  mkdir -p "$(dirname "$LABWC_AUTOSTART")"
  touch "$LABWC_AUTOSTART"

  # 幂等更新本项目管理的区块，并清理旧手工配置中指向同一页面的 Chromium 行。
  sed -i '/^# BEGIN EC EXPERIMENT KIOSK$/,/^# END EC EXPERIMENT KIOSK$/d' "$LABWC_AUTOSTART"
  sed -i '/chromium.*http:\/\/localhost:5173/d' "$LABWC_AUTOSTART"
  cat >> "$LABWC_AUTOSTART" <<EOF

# BEGIN EC EXPERIMENT KIOSK
sleep 5 && ${CHROMIUM_BIN} ${KIOSK_ARGS} http://localhost:5173 &
# END EC EXPERIMENT KIOSK
EOF

  # 迁移旧版 setup.sh 生成的入口，避免 Labwc 同时启动两个 Chromium。
  LEGACY_DESKTOP="${DEPLOY_HOME}/.config/autostart/ec-kiosk.desktop"
  if [ -f "$LEGACY_DESKTOP" ] && grep -Fq "Name=EC Experiment Kiosk" "$LEGACY_DESKTOP"; then
    rm -f "$LEGACY_DESKTOP"
  fi
  echo "    已写入 Labwc 自启动：$LABWC_AUTOSTART"
else
  # 兼容仍使用 LXDE/X11 的旧版 Raspberry Pi OS。
  XDG_AUTOSTART_DIR="${DEPLOY_HOME}/.config/autostart"
  mkdir -p "$XDG_AUTOSTART_DIR"
  cat > "${XDG_AUTOSTART_DIR}/ec-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=EC Experiment Kiosk
Comment=Open EC experiment UI in kiosk mode on login
Exec=${CHROMIUM_BIN} ${KIOSK_ARGS} http://localhost:5173
X-GNOME-Autostart-enabled=true
EOF
  echo "    已写入 XDG 自启动：${XDG_AUTOSTART_DIR}/ec-kiosk.desktop"
fi

echo "==> 部署完成。重启树莓派后将自动全屏打开 http://localhost:5173"
echo "    如需连接真实后端，编辑 frontend/.env.local 后重新执行本脚本。"
