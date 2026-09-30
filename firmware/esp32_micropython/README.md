# ESP32 I–V 台架采集固件（MicroPython）

树莓派上用 Thonny 烧进 ESP32 的采集程序，外加树莓派侧的串口采集脚本。ESP32 读 ADS1256（U/I）、驱动 MCP4728（双极性方波激励）、读 DS18B20（T），每帧输出一行 JSON，字段名对齐 [`docs/接入数据格式.md`](../../docs/接入数据格式.md) 的 Raw 层与溯源字段。

**v2 直接读这个串口**（`EC_DEVICE=serial`），设备的质量标志随帧进入判稳，见第 5.1 节；原项目经 `capture_serial.py` 存成 CSV 回放（第 5.2 节）。

| 文件 | 在哪跑 | 作用 |
|---|---|---|
| `main.py` | ESP32（MicroPython） | 器件初始化与自检、方波激励、U/I/T 采样、质量标志、JSON 帧输出；附 REPL 台架工具 |
| `capture_serial.py` | 树莓派（python3 + pyserial） | 串口帧存成 `.jsonl`（全字段）、`.csv`（回放格式）和 `.log`（设备日志） |

**边界**：固件只产出 U/I/T 和 `quality_flags`，不算 G/κ(T)/κ25（计算链在主机：v2 的 `ec/chemistry.py`，原项目的 `measurement.py`），也不做通道校准（Raw 按标称 VREF、PGA、`R_SHUNT_OHM` 换算）。电池常数 Kcell 由平台用标准液标定。

## 1. 接线（经典 ESP32 台架板）

| 器件 | 引脚 | 接 ESP32 / 电源 |
|---|---|---|
| ADS1256 | VCC / GND | 5V / GND |
| | SCLK / DIN / DOUT / CS / DRDY | GPIO18 / GPIO23 / GPIO19 / GPIO5 / GPIO16 |
| | /RESET、/PDWN、/SYNC | 3V3（悬空会状态不定） |
| MCP4728 | VCC / GND | 3V3 / GND |
| | SDA / SCL / LDAC | GPIO21 / GPIO22 / GND |
| DS18B20 | VCC / GND / DQ | 3V3 / GND / GPIO4，DQ 加 4.7 kΩ 上拉到 3V3 |

所有 GND 共地。模拟前端的接法如下。VB 输出中点电压（1.024 V）作虚拟地，VA 在「中点 ± 幅值」之间翻转，电池两端就是正负交替的方波：

```text
MCP4728 VA ──●──────────────── 电极 1 ┐
             └─ AIN0                   │  导电池 / 溶液（台架阶段先用精密电阻代替）
             ┌─ AIN1                   │
             ├─ AIN2                   │
      ┌──────●──────────────── 电极 2 ┘
      │
  [R_SHUNT]      电流采样电阻，阻值填到 R_SHUNT_OHM
      │
      ●─ AIN3
MCP4728 VB ──┘   中点 1.024 V（虚拟地）

U = AIN0 − AIN1（电池两端）    I = (AIN2 − AIN3) / R_SHUNT_OHM
```

- 采样电阻的下端必须接 VB，不能接 GND。接 GND 时电池上会叠加约 1 V 直流。这低于水的分解电压（1.23 V），不会明显析气，但电极会持续极化、读数随时间下降，还可能发生溶解氧还原、电极金属溶出等副反应。
- 1.024 V 是对地共模电平，电池本身只看到 VA−VB。所以浸在溶液里的温度探头外壳、金属容器等不能与电路地导通，否则共模电平会经它们形成直流通路。可以用万用表量一下探头外壳到 GND 是否开路。
- R_SHUNT 取与电池阻抗同一量级，U 和 I 两路共用一个 PGA。例：1413 µS/cm、Kcell≈1 的电池约 0.7 kΩ，配 1 kΩ 采样电阻。
- MCP4728 直接驱动时，回路电流建议 ≤1 mA（幅值 / (R_cell + R_SHUNT)）。电流更大或负载阻抗更低时，要加运放缓冲。
- 引脚都集中在 `main.py` 顶部「配置」区。换 ESP32-S3 时要改引脚（S3 没有 GPIO22~25）。

## 2. 配置（`main.py` 顶部）

| 配置 | 缺省 | 说明 |
|---|---|---|
| `R_SHUNT_OHM` ★ | 1000.0 | **必须与实物一致**，I 由它换算 |
| `U_CH` / `I_CH` | (0,1) / (2,3) | 差分对（正端, 负端），8 = AINCOM；读数为负就对调正负端 |
| `EXC_FREQ_HZ` / `EXC_AMPLITUDE_V` | 10 / 0.2 | 方波频率与幅值（VA−VB 的半峰峰值）。溶液测量幅值宜小 |
| `CYCLES_PER_FRAME` / `WARMUP_CYCLES` / `FRAME_PERIOD_MS` | 4 / 1 / 1000 | 每帧先跑 1 个预热周期（不计入），再跑 4 个计入周期，然后电池回 0 V 静息，1 Hz 出帧 |
| `ADS_PGA` / `ADS_DRATE_SPS` / `ADS_BUFFER` | 1 / 1000 / True | 缓冲打开时，各输入须在 0~3.0 V |
| `DEVICE_ID` | None | None 时按芯片 MAC 生成 `ESP32-IV-xxxxxx`，多块板不用逐块改 |

启动时固件会先校验配置，再实测单次读数耗时。半周期内放不下「稳定等待 + 采样」时，或者帧周期内放不下一帧激励时，固件直接报错、不启动。它不会带着错误的频率继续跑。

## 3. 用 Thonny 烧录（树莓派）

1. ESP32 插到树莓派 USB 口（`/dev/ttyUSB0`），板上需已刷好 MicroPython。
2. Thonny：运行 → 配置解释器 → **MicroPython (ESP32)**，端口选 `/dev/ttyUSB0`。在 Shell 里执行 `import sys; sys.platform`，应显示 `'esp32'`。
3. 打开 `main.py`，按实物修改配置。
4. **先探测**：按 F5 运行一次，Shell 里依次出现三个器件的自检结果，随后开始出帧；按 Stop 停止。也可以只做探测、不加激励：在 Shell 里输入 `import main`，再输入 `main.probe()`。
5. **固化**：文件 → 另存为… → MicroPython 设备 → 文件名填 `main.py`。之后一上电（或按板上 EN 键）就会自动开始输出，不需要 Thonny。
6. **交给平台**：关掉 Thonny（或 运行 → 断开），再启动 v2 或 `capture_serial.py`。Thonny 连接时会中断 `main.py`，断开后板子停在 REPL、不会自己再跑；v2 和 `capture_serial.py` 打开串口时都会发 Ctrl-C、Ctrl-B、Ctrl-D 让板子软重启，`main.py` 从器件自检重新开始，不用手按 EN。

Shell 里的输出长这样：以 `# ` 开头的是日志，其余每行是一帧。

```text
# INFO MCP4728 正常：addr=0x60，VA=VB=1.024 V（电池两端 0 V）
# INFO ADS1256 正常：STATUS=0x30 ID=3，1000 SPS，PGA=1，缓冲开，已自校准
# INFO DS18B20 正常：rom=28ff641e0f21035c，T=25.0625 °C
# INFO 时序：单次读数 1265 µs；半周期 50000 µs（前 30000 µs 等稳定）；每帧激励 500 ms / 帧周期 1000 ms
{"schema_version":2,"seq_no":1,"monotonic_ms":0,"voltage_raw_v":0.0999999,"current_raw_a":9.99999e-05,"temperature_raw_c":25.0625,"quality_flags":null,"device_id":"ESP32-IV-3C71BF","firmware_version":"0.1.0-mpy","range_id":"RS1000R_G1","excitation_frequency_hz":10.0,"excitation_amplitude_v":0.2}
```

REPL 台架工具（先按 Stop，再 `import main`）：

| 调用 | 作用 |
|---|---|
| `main.probe()` | 探测三个器件并给出排查提示，不加激励 |
| `main.volts(0, 1)` | 读一次 AIN0−AIN1；`main.volts(0, 8)` 读 AIN0 对 AINCOM |
| `main.dc(0.1)` / `main.rest()` | 在电池两端加 +0.1 V 静态电压，用万用表核对 / 回到 0 V。**只用于电阻负载** |
| `main.run(10)` | 采 10 帧后停止 |

## 4. 帧字段

| 字段 | 单位 | 含义 | 对应接入规格 |
|---|---|---|---|
| `voltage_raw_v` | V | U：电池两端方波幅值 = (正半周均值 − 负半周均值) / 2 | Raw U |
| `current_raw_a` | A | I：采样电阻压降幅值 / `R_SHUNT_OHM` | Raw I |
| `temperature_raw_c` | °C | DS18B20 最近一次有效读数（不超过 3 s）；无效时为 null | Raw T |
| `quality_flags` | — | `\|` 分隔，无标志时为 null | §5 质量标志 |
| `seq_no` | — | 设备帧序号，每次启动从 1 开始，跳号 = 串口丢行 | 溯源（后端落库时按实验重新编号） |
| `monotonic_ms` | ms | 设备单调时钟，从 `run()` 启动时起算，已处理 ticks 回绕 | 溯源 |
| `schema_version` / `device_id` / `firmware_version` / `range_id` | — | 2 / 设备号 / `0.1.0-mpy` / `RS<采样电阻>R_G<PGA>` | 溯源 |
| `excitation_frequency_hz` / `excitation_amplitude_v` | Hz / V | 方波频率 / DAC 设定幅值（已按 12 位量化） | 溯源 |

以下字段不由固件产出：`timestamp`/`t_seconds`（实验相对时间，后端计）、`timestamp_utc`（`capture_serial.py` 以主机接收时刻写入 `.jsonl`）、`sensor_path_id`/`calibration_id`/`compensation_model`（开始实验时由后端决定），以及 G/κ(T)/κ25。

`G = I/U` 的取法（规格要求硬件冻结时唯一确定）在本固件中定为**方波平台段幅值法**：每个半周期先等 60% 时长让波形稳定，再按 U I I U 交错采样，让 U 与 I 的时间重心重合；正负半周相减，恒定零点偏置随之抵消。

质量标志：

| 标志 | 条件 | 判稳 |
|---|---|---|
| `SATURATED` | 任一采样码值 ≥ 满量程 99.9% | 硬异常 → FAIL |
| `OPEN_CIRCUIT` | 采样电阻压降幅值 < 50 µV | 硬异常 → FAIL |
| `SHORT_CIRCUIT` | U 幅值 < 激励幅值的 1% | 硬异常 → FAIL |
| `WAVEFORM_UNSTABLE` | 正负半周不对称度 > 10%，或采样拖过了半周期 | 提示 |
| `TEMP_INVALID` | 无探头、CRC 错、全 0、85 °C 上电值、超出 −10~100 °C，或读数超过 3 s | 温度为 null，后端视为不完整帧 |
| `DROPOUT` | ADC/DAC 通信失败（U/I 为 null）；连续 3 帧失败后自动重新初始化 | 后端视为不完整帧 |

阈值是台架标定前的保守值，在 `main.py` 配置区调整。

## 5. 接入平台

两条路都要先在 Thonny 里断开设备（运行 → Disconnect，或直接关掉 Thonny），否则串口被占用。

### 5.1 v2：直接读串口（推荐）

v2 的安装与树莓派部署见 [`v2/README.md`](../../v2/README.md)。装好后：

```bash
cd ~/electrochem-platform/v2/backend
EC_DEVICE=serial EC_SERIAL_PORT=/dev/ttyUSB0 .venv/bin/python -m ec
```

浏览器打开 `http://127.0.0.1:8000`。

- 连上后，页面顶栏的设备状态先显示「正在重启固件」，接着依次显示三个器件的自检日志，然后开始出读数。v2 每次打开串口都会让板子软重启（见第 3 节第 6 步），所以 `main.py` 必须已经存到设备上。
- 设备质量标志直接进判稳：`SATURATED`、`OPEN_CIRCUIT`、`SHORT_CIRCUIT`、`DROPOUT` 判 FAIL，`WAVEFORM_UNSTABLE` 判 WARN。温度无效的帧算不出 κ25，计为无效帧。U≤0 或 I<0 由 v2 标 `POLARITY`（FAIL）。
- 帧里的 `device_id`、`firmware_version`、`range_id`、激励频率和幅值会在开始测量时存进参数快照，日后能查到每次测量用的是哪块板、哪个采样电阻。
- 实验顺序：先在「标定」页用标准液标定 Kcell，再测样品。见 v2 README 的「一次典型的实验」。

### 5.2 存采集文件：`capture_serial.py`

```bash
python3 capture_serial.py --out ~/runs/r1k_bench --seconds 600
```

- 生成 `r1k_bench.jsonl`（设备帧 + `timestamp_utc`）、`r1k_bench.csv`、`r1k_bench.log`；stderr 逐帧打印进度，结束时 stdout 输出一行 JSON 汇总（帧数、完整行数、跳号、设备重启次数、各标志计数）。
- 退出码：0 = 至少 1 行完整 U/I/T；1 = 没有完整帧；2 = 串口或参数错误，或输出文件已存在（脚本不覆盖原始数据，请换一个 `--out`）。
- 打开串口时脚本会让板子软重启一次（与 v2 相同）。板子原本就在出帧时，重启前后的帧都会收到，汇总里 `device_restarts` 记 1，属正常：脚本识别设备重启后时间轴接续，不会倒流。
- 保存下来的 Thonny 输出也能转换：`python3 capture_serial.py --input shell.txt --out ~/runs/x`。
- `.jsonl` 可以在 v2 里原样回放，质量标志都在：`EC_DEVICE=replay EC_REPLAY_PATH=$HOME/runs/r1k_bench.jsonl .venv/bin/python -m ec`。

原项目回放 CSV（`EC_CSV_SAMPLE_RATE_HZ` 要与帧率一致，否则同一帧会被重复读成多帧）：

```bash
EC_DRIVER=csv EC_CSV_PATH=$HOME/runs/r1k_bench.csv EC_CSV_SAMPLE_RATE_HZ=1 EC_CELL_CONSTANT=1.0 scripts/run_backend.sh
```

**CSV 路径的限制**：`CsvPlaybackDriver` 只读前 4 列，并统一标 `CSV|PLAYBACK|UNCALIBRATED`，设备的 `quality_flags` 不会进入平台。所以回放前要看 CSV 的 `quality_flags` 列或采集汇总，确认没有 `OPEN_CIRCUIT`/`SHORT_CIRCUIT`/`SATURATED`。要带着质量标志完整接入，用 5.1 的 v2 串口设备。

## 6. 台架自检顺序

1. `main.probe()`：三个器件都正常。
2. 零点：AIN0 短接 AINCOM，`main.volts(0, 8)` 应接近 0（自校准后一般 <1 mV）。
3. 用精密电阻 R 代替导电池，`main.dc(0.1)`，再用万用表核对电极两端电压和采样电阻压降，分别与 `main.volts(0, 1)`、`main.volts(2, 3)` 对照；用完执行 `main.rest()`。
4. 运行采集：`current_raw_a / voltage_raw_v` 应约等于 1/R（1 kΩ → 1.000e-3 S）。重复测量，并换几档电阻看线性。
5. 换上导电池，先做下面第 7 节的极化检查，再用标准液在 v2「标定」页标定 Kcell。

## 7. 已知限制

- U、I 由同一个 ADS1256 多路切换**分时**采样。交错采样只能对齐时间重心，不是同步采样；路线图 N7 要求的 U/I 同步偏差 <1 ms，要到后续 ESP32-S3 固件才能满足。
- MicroPython + I2C DAC 下，方波频率实用上限约几十 Hz，kHz 级激励需要硬件波形发生。本固件的定位是台架验证与低频探索，实际频率随帧记录在 `excitation_frequency_hz`。
- **低频极化误差**：电极界面的双电层相当于与溶液电阻串联的电容，频率越低、溶液电导越高，读数越偏低。按光面裸电极（约 1 cm²，双电层 10~50 µF）估算，10 Hz 测 1413 µS/cm 可能偏低 25%~86%；电阻负载没有这个问题，镀铂黑电极可降到 0.2% 左右。上溶液前要在台架上实测判定：同一溶液分别用 `SETTLE_FRACTION` 0.35 和 0.9、或 `EXC_FREQ_HZ` 5/10/20 Hz 各采一段，G 不随采样点和频率变化才可信。极化造成的偏低是**稳定的**偏低，v2 判稳照样显示「稳定」：判稳只看读数稳不稳，不看准不准。用电导率接近样品的标准液标定能吸收一部分，但偏低程度随电导率变化，浓度系列（线性标定、Kohlrausch 拟合）会被扭曲。
- 激励按帧突发：每帧激励约 500 ms，其余时间电池两端为 0 V。每个周期正负对称，净直流为 0。
- 本固件只在 CPython 上用模拟硬件（ADS1256 协议/时序、MCP4728、DS18B20、电阻网络、ticks 回绕）验证过逻辑，并把输出经 `capture_serial.py` → CSV → 后端 CSV 驱动 + 计算链核对过数值。**还没在实物上跑过**，首次上板请按第 6 节从 `probe()` 开始。

## 8. 排障速查

| 现象 | 先查 |
|---|---|
| ADS1256 `STATUS=0x00` | **先查模块 5V 供电**：未供电时芯片经 ESD 钳位把所有线拉到低电平，DRDY 看起来也「正常拉低」。供电正常再查 DOUT→GPIO19、CS→GPIO5 |
| ADS1256 `STATUS=0xFF` | MISO 恒高：查接线、SPI 模式 |
| ADS1256 自校准超时 | DRDY→GPIO16 断线（一直为高） |
| 未找到 MCP4728 | 日志里的 I2C scan 结果；SDA/SCL 是否接反；VDD/GND；上拉电阻 |
| DS18B20 未就绪 | DQ→GPIO4 与 4.7 kΩ 上拉；防水探头线色没有统一标准，要用万用表确认 |
| 帧里常驻 `WAVEFORM_UNSTABLE` | 设 `DIAG = True` 看 `u_pos_v`/`u_neg_v`：两者同号，说明采样电阻下端接了 GND 而不是 VB |
| U 或 I 为负 | 接线方向反了，对调 `U_CH`/`I_CH` 的正负端（v2 标 `POLARITY`，原项目标 `COMPUTE_INVALID`） |
| v2 / `capture_serial.py` 打不开串口 | Thonny 是否还连着设备；当前用户是否在 dialout 组 |
| v2 显示串口已打开，但一直没有读数 | `main.py` 是否已存到设备上（Thonny 文件面板里「MicroPython 设备」下应有 `main.py`）；顶栏设备状态若有 `ERROR` 日志，按提示查；仍没有输出就按一下板上 EN 键 |
| 运行或导入时报 `MemoryError` | `main.py` 约 30 KB，要在板上现场编译，无 PSRAM 的板内存偏紧时可能失败。改为预编译：把 `main.py` 改名为 `ec_iv.py`，用与板上 MicroPython 同版本的 `mpy-cross` 编译成 `ec_iv.mpy` 并上传，再新建一个 `main.py`，只写两行：`import ec_iv` 和 `ec_iv.run()` |
