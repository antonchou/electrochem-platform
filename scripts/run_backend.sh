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
exec "${ROOT}/backend/.venv/bin/python" -m uvicorn app.main:app --host "$BIND" --port "$PORT"
