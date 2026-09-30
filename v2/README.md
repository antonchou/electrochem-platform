# 电导率实验平台 v2

对原平台的整体重写，与仓库根目录的原项目并存、互不依赖。为什么重写、改了什么、取舍是什么，见 [docs/设计说明.md](docs/设计说明.md)；接口、设备帧、数据库与环境变量见 [docs/接口.md](docs/接口.md)。

一句话：设备（ESP32 或模拟器）推来原始 U/I/T，平台算出 κ25、判断读数是否稳定、存下原始数据；用标准液标定电池常数 Kcell；把多次测量放在一起比较并做浓度拟合。

## 快速开始（模拟导电池，无需硬件）

需要 Python ≥ 3.11、Node ≥ 22.6。

```bash
cd v2/backend
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt      # Windows：.venv\Scripts\pip
cd ../frontend
npm install
npm run build                                        # 产物在 frontend/dist，由后端托管
cd ../backend
.venv/bin/python -m ec                               # 打开 http://127.0.0.1:8000
```

一次典型的实验：

1. **标定**：「测量」页填样品名「KCl 0.01 mol/L」、浓度 10，开始测量，稳定性显示「稳定」后停止；到「标定」页选中这次测量和标准液「KCl 0.01 mol/L」，启用新标定。
2. **测样品**：换样品，逐个测量（每次等到稳定再停）。
3. **比较**：「记录」页勾选几次测量，看 κ25 比较；有浓度的测量可以做线性标定与 Kohlrausch 拟合。

模拟器的真实 Kcell 是 1.02（故意与标称 1.0 不同），所以能看到标定前后读数的变化。故障演示：`curl -X POST localhost:8000/api/simulator -H 'content-type: application/json' -d '{"fault":"air"}'`（电极出水 → 开路标志）。

## 接真实设备

- **串口（ESP32 台架固件）**：`EC_DEVICE=serial EC_SERIAL_PORT=/dev/ttyUSB0 python -m ec`。需要 `pyserial`（`requirements.txt` 已含；树莓派也可 `sudo apt install python3-serial`）。先在 Thonny 里断开设备，否则串口被占用。设备日志（器件自检结果等）会显示在页面顶栏设备状态的提示里。
- **回放采集文件**：`EC_DEVICE=replay EC_REPLAY_PATH=../samples/simulated_kcl_10mM.jsonl python -m ec`。`capture_serial.py` 生成的 `.jsonl` 都能直接回放（按设备时钟的节奏，`EC_REPLAY_SPEED` 可加速）。

## 开发

```bash
cd v2/backend && .venv/bin/python -m ec             # 后端 :8000
cd v2/frontend && npm run dev                        # 前端 :5174，/api 与 /ws 代理到 :8000
```

## 测试

```bash
cd v2/backend && .venv/bin/python -m pytest -q       # 后端
cd v2/frontend && npm run typecheck && npm test      # 前端类型检查与单测
cd v2/frontend && npm run build && EC_PYTHON=../backend/.venv/bin/python npx playwright test   # E2E
```

E2E 按生产形态跑：Playwright 在 :8011 启动后端并托管 `dist`（模拟器 10 Hz、判稳窗口 2 s），数据库放在系统临时目录。

## 部署（树莓派）

```bash
cd v2/frontend && npm ci && npm run build
cd ../backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

systemd 服务（`/etc/systemd/system/ec-v2.service`，按实际路径与用户修改）：

```ini
[Unit]
Description=Conductivity platform v2
Wants=network-online.target
After=network-online.target

[Service]
User=pi
WorkingDirectory=/home/pi/electrochem-platform/v2/backend
Environment=EC_DEVICE=serial
Environment=EC_HOST=0.0.0.0
ExecStart=/home/pi/electrochem-platform/v2/backend/.venv/bin/python -m ec
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Chromium Kiosk 自启可沿用原项目 `scripts/deploy/setup.sh` 的写法，把地址指向 `http://127.0.0.1:8000`。

## 目录

```
v2/
├── backend/ec/         后端（模块职责见设计说明 §3）
├── backend/tests/      pytest
├── frontend/src/       前端：lib/（纯逻辑 + 单测）、pages/、components/
├── frontend/e2e/       Playwright
├── samples/            示例采集文件（固件帧格式）
└── docs/               设计说明、接口
```
