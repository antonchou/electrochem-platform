#!/usr/bin/env bash
# 树莓派一键部署：构建前端 + 安装依赖 + 安装 systemd 服务 + Kiosk 自启动
# 用法：以桌面登录用户执行 ./scripts/deploy/setup.sh（不要在脚本前加 sudo）
#
# 前端由 FastAPI 在同源端口（EC_PORT，默认 8000）托管 frontend/dist，不再单独起 :5173。
set -euo pipefail

if [ "${EUID}" -eq 0 ]; then
  echo "错误：请以树莓派桌面登录用户运行本脚本，不要使用 sudo ./setup.sh。"
  echo "      脚本会在安装 systemd 服务时自行调用 sudo。"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
DEPLOY_USER="$(id -un)"
DEPLOY_HOME="${HOME}"
EC_BIND="${EC_BIND:-127.0.0.1}"
# 端口必须在最前面定死（P1-1）：systemd unit、kiosk launcher 与全部文案共用同一个值，
# 半途定义会让前半段文案与后半段部署目标不一致。
EC_PORT="${EC_PORT:-8000}"
if ! [[ "$EC_PORT" =~ ^[0-9]+$ ]] || [ "$EC_PORT" -lt 1 ] || [ "$EC_PORT" -gt 65535 ]; then
  echo "错误：无效 EC_PORT=${EC_PORT}（须为 1-65535 的整数）"
  exit 1
fi
case "$EC_BIND" in
  127.0.0.1 | 0.0.0.0 | localhost) ;;
  *)
    if [[ ! "$EC_BIND" =~ ^((25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])$ ]]; then
      echo "错误：无效 EC_BIND=${EC_BIND}（允许 127.0.0.1、0.0.0.0、localhost 或 IPv4）"
      exit 1
    fi
    ;;
esac

# Kiosk 访问后端用的主机（R3-7）：输出 "<页面主机> <就绪探测主机>"。
# 绑回环/全部网卡时走本机回环；绑到具体网卡 IP 时 uvicorn 不监听回环，
# 就绪探测与页面地址都必须用这个 IP，否则 Kiosk 等满 60s 后打开一个连不上的页面。
kiosk_hosts() {
  case "$1" in
    127.0.0.1 | 0.0.0.0 | localhost) echo "localhost 127.0.0.1" ;;
    *) echo "$1 $1" ;;
  esac
}
read -r KIOSK_HOST HEALTH_HOST <<<"$(kiosk_hosts "$EC_BIND")"
echo "==> 项目目录: $PROJECT_DIR"
echo "==> 部署用户: $DEPLOY_USER"
echo "==> 监听地址: ${EC_BIND}:${EC_PORT}"

# ---------- 1. 前端依赖与构建 ----------
echo "[1/3] 构建前端到 frontend/dist（由后端 :${EC_PORT} 托管）"
cd "$PROJECT_DIR/frontend"
# npm ci 每次执行（T-32）：只在 node_modules 缺失时安装会让升级后的构建
# 继续使用陈旧依赖出包。npm ci 严格按 lock 安装，代价是可预期的重装时间。
# 离线重部署（P2-8）：EC_SKIP_NPM_INSTALL=1 可跳过；npm ci 失败且已有
# node_modules 时降级为警告继续，保证 systemd/kiosk 配置步骤不被中断。
if [ "${EC_SKIP_NPM_INSTALL:-0}" = "1" ]; then
  echo "    EC_SKIP_NPM_INSTALL=1：跳过前端依赖安装（离线重部署模式）"
elif [ -f package-lock.json ]; then
  npm ci || {
    if [ -d node_modules ]; then
      echo "警告：npm ci 失败（可能离线），复用已有 node_modules 继续。"
      echo "      依赖可能陈旧；联网后重跑或显式 EC_SKIP_NPM_INSTALL=1 跳过本步。"
    else
      echo "错误：npm ci 失败且没有已有的 node_modules，无法继续。" >&2
      exit 1
    fi
  }
else
  npm install
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
# 生产环境不安装 pytest/httpx；开发机另装 requirements-dev.txt

# ---------- 3. systemd：只保留 ec-backend ----------
echo "[3/3] 安装 systemd 服务（页面 + API + WebSocket 都在 :${EC_PORT}）"
UNIT_DIR="$(mktemp -d)"
SVC_BACKEND="${UNIT_DIR}/ec-backend.service"

chmod +x "${PROJECT_DIR}/scripts/run_backend.sh"
cat > "$SVC_BACKEND" <<EOF
[Unit]
Description=EC Experiment (FastAPI + frontend)
# 绑定具体网卡 IP 时要等地址就绪，否则开机时 uvicorn 先绑定失败、靠 Restart 反复重试
Wants=network-online.target
After=network-online.target

[Service]
User=${DEPLOY_USER}
WorkingDirectory=${PROJECT_DIR}/backend
Environment=EC_BIND=${EC_BIND}
Environment=EC_PORT=${EC_PORT}
ExecStart=${PROJECT_DIR}/scripts/run_backend.sh
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
# 注意 list-unit-files 对不存在的单元也返回 0（T-31），必须判输出非空。
if [ -n "$(systemctl list-unit-files ec-web.service --no-legend 2>/dev/null)" ]; then
  sudo systemctl disable --now ec-web.service >/dev/null 2>&1 || true
  sudo rm -f /etc/systemd/system/ec-web.service
fi

sudo systemctl daemon-reload
sudo systemctl enable ec-backend
sudo systemctl restart ec-backend
echo "    服务已启用并重启：ec-backend → http://${EC_BIND}:${EC_PORT}"
if [ "$EC_BIND" = "127.0.0.1" ] || [ "$EC_BIND" = "localhost" ]; then
  echo "    当前仅本机 Kiosk 可访问。教师机/平板：EC_BIND=0.0.0.0 ./scripts/deploy/setup.sh"
  echo "    或 sudo systemctl edit ec-backend 增加 Environment=EC_BIND=0.0.0.0 后 restart"
fi

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
# EC_PORT 已在脚本头部定义（P1-1）；kiosk 与后端用同一端口，主机见 kiosk_hosts（R3-7）。
KIOSK_URL="http://${KIOSK_HOST}:${EC_PORT}"

# 就绪轮询启动器（T-29）：冷启动时后端可能尚未监听，硬编码 sleep 5 会让
# Chromium 打开错误页且不重试。等待 /health（或 TCP 端口）就绪后再打开。
KIOSK_DIR="${DEPLOY_HOME}/.config/ec-kiosk"
LAUNCHER="${KIOSK_DIR}/launch-kiosk.sh"
mkdir -p "$KIOSK_DIR"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Auto-generated by scripts/deploy/setup.sh — do not edit; rerun setup.sh instead.
for _ in \$(seq 1 60); do
  if command -v curl >/dev/null 2>&1; then
    curl -fsS "http://${HEALTH_HOST}:${EC_PORT}/health" >/dev/null 2>&1 && break
  else
    (exec 3<>"/dev/tcp/${HEALTH_HOST}/${EC_PORT}") 2>/dev/null && { exec 3>&- 3<&-; break; }
  fi
  sleep 1
done
exec ${CHROMIUM_BIN} ${KIOSK_COMMON} \$EXTRA_KIOSK_ARGS ${KIOSK_URL}
EOF
chmod +x "$LAUNCHER"

if command -v labwc >/dev/null 2>&1; then
  EXTRA_KIOSK_ARGS="--ozone-platform=wayland"
  LABWC_AUTOSTART="${DEPLOY_HOME}/.config/labwc/autostart"
  mkdir -p "$(dirname "$LABWC_AUTOSTART")"
  touch "$LABWC_AUTOSTART"

  sed -i '/^# BEGIN EC EXPERIMENT KIOSK$/,/^# END EC EXPERIMENT KIOSK$/d' "$LABWC_AUTOSTART"
  # 仅清理本脚本历史版本写入的 kiosk 行（带 --kiosk 标记），避免误删用户自定义行（P3-7）
  sed -i '/chromium .*--kiosk.*http:\/\/localhost:5173/d' "$LABWC_AUTOSTART"
  sed -i '/chromium .*--kiosk.*http:\/\/localhost:8000/d' "$LABWC_AUTOSTART"
  cat >> "$LABWC_AUTOSTART" <<EOF

# BEGIN EC EXPERIMENT KIOSK
EXTRA_KIOSK_ARGS="--ozone-platform=wayland" "${LAUNCHER}" &
# END EC EXPERIMENT KIOSK
EOF

  LEGACY_DESKTOP="${DEPLOY_HOME}/.config/autostart/ec-kiosk.desktop"
  if [ -f "$LEGACY_DESKTOP" ] && grep -Fq "Name=EC Experiment Kiosk" "$LEGACY_DESKTOP"; then
    rm -f "$LEGACY_DESKTOP"
  fi
  echo "    已写入 Labwc 自启动：$LABWC_AUTOSTART"
else
  XDG_AUTOSTART_DIR="${DEPLOY_HOME}/.config/autostart"
  mkdir -p "$XDG_AUTOSTART_DIR"
  cat > "${XDG_AUTOSTART_DIR}/ec-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=EC Experiment Kiosk
Comment=Open EC experiment UI in kiosk mode on login (waits for backend /health)
Exec=env EXTRA_KIOSK_ARGS="--start-maximized" ${LAUNCHER}
X-GNOME-Autostart-enabled=true
EOF
  echo "    已写入 XDG 自启动：${XDG_AUTOSTART_DIR}/ec-kiosk.desktop"
fi

echo "==> 部署完成。重启树莓派后将自动全屏打开 ${KIOSK_URL}"
echo "    开发联调仍可用：cd frontend && npm run dev  （Vite :5173，API 仍连 :${EC_PORT}）"
