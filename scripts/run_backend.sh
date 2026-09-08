#!/usr/bin/env bash
# 启动 FastAPI（页面 + API + WebSocket）。
# 默认只绑本机；局域网访问：EC_BIND=0.0.0.0
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIND="${EC_BIND:-127.0.0.1}"
PORT="${EC_PORT:-8000}"
case "$BIND" in
  127.0.0.1 | 0.0.0.0 | localhost) ;;
  *)
    if [[ ! "$BIND" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
      echo "无效 EC_BIND=${BIND}（允许 127.0.0.1、0.0.0.0、localhost 或 IPv4）" >&2
      exit 1
    fi
    ;;
esac
cd "$ROOT/backend"
# venv 布局跨平台：Linux/树莓派为 bin/，Windows（Git Bash）为 Scripts/
if [ -x "${ROOT}/backend/.venv/bin/python" ]; then
  PY="${ROOT}/backend/.venv/bin/python"
elif [ -f "${ROOT}/backend/.venv/Scripts/python.exe" ]; then
  PY="${ROOT}/backend/.venv/Scripts/python.exe"
else
  echo "错误：未找到 backend/.venv（先在 backend/ 下创建虚拟环境）" >&2
  exit 1
fi
exec "$PY" -m uvicorn app.main:app --host "$BIND" --port "$PORT"
