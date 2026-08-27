#!/usr/bin/env bash
# 用 SimulatorDriver 启动后端（不改生产 kiosk 默认 mock）。
# 用法（仓库根目录）：
#   ./scripts/run_simulator.sh
#   EC_SIM_MODE=realistic ./scripts/run_simulator.sh
#   EC_SIM_CONFIG=configs/devices/simulator.fault.json ./scripts/run_simulator.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export EC_DRIVER="${EC_DRIVER:-simulator}"
export EC_SIM_CONFIG="${EC_SIM_CONFIG:-$ROOT/configs/devices/simulator.example.json}"
cd "$ROOT/backend"
exec "${ROOT}/backend/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
