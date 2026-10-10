# ESP32 I–V 台架采集固件（MicroPython）

树莓派上用 Thonny 烧进 ESP32 的采集程序，外加树莓派侧的串口采集脚本。ESP32 读 ADS1256（U/I）、驱动 MCP4728（双极性方波激励）、读 DS18B20（T），每帧输出一行 JSON，字段名对齐 [`docs/接入数据格式.md`](../../docs/接入数据格式.md) 的 Raw 层与溯源字段。

**v2 直接读这个串口**（`EC_DEVICE=serial`），设备的质量标志随帧进入判稳，见第 5.1 节；原项目经 `capture_serial.py` 存成 CSV 回放（第 5.2 节）。

| 文件 | 在哪跑 | 作用 |
|---|---|---|
| `main.py` | ESP32（MicroPython） | 器件初始化与自检、方波激励、U/I/T 采样、质量标志、JSON 帧输出；附 REPL 台架工具 |
| `capture_serial.py` | 树莓派（python3 + pyserial） | 串口帧存成 `.jsonl`（全字段）、`.csv`（回放格式）和 `.log`（设备日志） |
| `bench/` | ESP32（Thonny F5 直接跑） | 树莓派上的旧版台架脚本，2026-10-10 原样同步，运行结果未经核实；见第 9 节和 [`bench/审查报告.md`](bench/审查报告.md) |

**边界**：固件只产出 U/I/T 和 `quality_flags`，不算 G/κ(T)/κ25（计算链在主机：v2 的 `ec/chemistry.py`，原项目的 `measurement.py`），也不做通道校准（Raw 按标称 VREF、PGA、`R_SHUNT_OHM` 换算）。电池常数 Kcell 由平台用标准液标定。

## 1. 接线（台架板）

台架板是 ESP32-S3（2026-10-10 确认），引脚与树莓派上 `bench/` 脚本所用的一致。换经典 ESP32 时 DIN 要改接 GPIO23（经典 ESP32 的 GPIO6~11 接片上 flash），`main.py` 的 `ADS_MOSI` 同步改。DOUT 接 GPIO13（10-10 决策 D2，原接 GPIO19）：S3 的 GPIO19/20 是原生 USB 的 D−/D+，占用后树莓派插原生 USB 口时 SPI 一初始化串口就会断开（审查报告 F5）。改接后插哪个 USB 口都行。

> **当前实物与下图不符**：采样电阻下端接的是 GND（10-10 确认），不是 VB。这样接时，`main.py` 一运行（包括 `probe()`），电池加采样电阻对地就有 1.024 V 直流，方波也不过零。上 `main.py` 前必须先把采样电阻下端改接到 MCP4728 VB（审查报告 F2、D1）。

| 器件 | 引脚 | 接 ESP32 / 电源 |
|---|---|---|
| ADS1256 | VCC / GND | 5V / GND |
| | SCLK / DIN / DOUT / CS / DRDY | GPIO18 / GPIO6 / GPIO13 / GPIO5 / GPIO16 |
| | /RESET、/PDWN、/SYNC | 3V3（悬空会状态不定） |
| MCP4728 | VCC / GND | 3V3 / GND |
| | SDA / SCL / LDAC | GPIO8 / GPIO9 / GND |
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
- R_SHUNT 取与电池阻抗同一量级，U 和 I 两路共用一个 PGA。例：1413 µS/cm、Kcell≈1 的电池约 0.7 kΩ，配 1 kΩ 采样电阻。台架用 10 kΩ（10-10 决策 D3）。按 Kcell≈1.5 估算，SRS 目标量程（NaCl 2~10 mmol/L 约 245~1175 µS/cm，中点 1413 µS/cm）内 U 幅值 ≥19 mV；大阻值还让方波期间电流近似恒定，有利于居中采样抵消极化（审查报告第七节）。代价是 ≳15 mS/cm 会被误判为 SHORT_CIRCUIT（F6），所以标定不用 12.88 mS/cm 标准液。换阻值时同步改 `R_SHUNT_OHM`。
- MCP4728 直接驱动时，回路电流建议 ≤1 mA（幅值 / (R_cell + R_SHUNT)）。电流更大或负载阻抗更低时，要加运放缓冲。
- 引脚都集中在 `main.py` 顶部「配置」区。

## 2. 配置（`main.py` 顶部）

| 配置 | 缺省 | 说明 |
|---|---|---|
| `R_SHUNT_OHM` ★ | 10000.0 | **必须与实物一致**，I 由它换算（台架现用 10 kΩ） |
| `U_CH` / `I_CH` | (0,1) / (2,3) | 差分对（正端, 负端），8 = AINCOM；读数为负就对调正负端 |
| `EXC_FREQ_HZ` / `EXC_AMPLITUDE_V` | 10 / 0.2 | 方波频率与幅值（VA−VB 的半峰峰值）。溶液测量幅值宜小 |
| `CYCLES_PER_FRAME` / `WARMUP_CYCLES` / `FRAME_PERIOD_MS` | 4 / 1 / 1000 | 每帧先跑 1 个预热周期（不计入），再跑 4 个计入周期，然后电池回 0 V 静息，1 Hz 出帧 |
| `ADS_PGA` / `ADS_DRATE_SPS` / `ADS_BUFFER` | 1 / 1000 / True | 缓冲打开时，各输入须在 0~3.0 V |
| `DEVICE_ID` | None | None 时按芯片 MAC 生成 `ESP32-IV-xxxxxx`，多块板不用逐块改 |

启动时固件会先校验配置，再实测单次读数耗时。半周期内放不下「稳定等待 + 采样」时，或者帧周期内放不下一帧激励时，固件直接报错、不启动。它不会带着错误的频率继续跑。

## 3. 用 Thonny 烧录（树莓派）

1. ESP32 插到树莓派 USB 口，板上需已刷好 MicroPython。端口以 `ls /dev/ttyUSB* /dev/ttyACM*` 为准：CH340/CP210x 串口芯片是 `/dev/ttyUSB0`，S3 原生 USB 或 CH343 一般是 `/dev/ttyACM0`；下文和 v2 的 `EC_SERIAL_PORT` 都按实际端口填。
2. Thonny：运行 → 配置解释器 → **MicroPython (ESP32)**，端口选上一步的设备。在 Shell 里执行 `import sys; sys.platform`，应显示 `'esp32'`。
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

- 串口打开后，设备先显示「未连接」，「测量」页的提示里依次出现「正在重启固件」和三个器件的自检日志；收到第一帧才变为已连接，「开始测量」随之可用。v2 每次打开串口都会让板子软重启（见第 3 节第 6 步），所以 `main.py` 必须已经存到设备上。出帧后连续 5 s 没有任何数据，设备会改回「未连接」。
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
- 树莓派上的旧版台架脚本（第 9 节）在实物上的运行结果未经核实；台架验证步骤见 [`bench/审查报告.md`](bench/审查报告.md) 第五节。
- 本固件只在 CPython 上用模拟硬件（ADS1256 协议/时序、MCP4728、DS18B20、电阻网络、ticks 回绕）验证过逻辑，并把输出经 `capture_serial.py` → CSV → 后端 CSV 驱动 + 计算链核对过数值。**`main.py` 本身还没在实物上跑过**，首次上板请按第 6 节从 `probe()` 开始。

## 8. 排障速查

| 现象 | 先查 |
|---|---|
| ADS1256 `STATUS=0x00` | **先查模块 5V 供电**：未供电时芯片经 ESD 钳位把所有线拉到低电平，DRDY 看起来也「正常拉低」。供电正常再查 DOUT→GPIO13、CS→GPIO5 |
| ADS1256 `STATUS=0xFF` | MISO 恒高：查接线、SPI 模式 |
| ADS1256 自校准超时 | DRDY→GPIO16 断线（一直为高） |
| 未找到 MCP4728 | 日志里的 I2C scan 结果；SDA→GPIO8、SCL→GPIO9 是否接反；VDD/GND；上拉电阻 |
| DS18B20 未就绪 | DQ→GPIO4 与 4.7 kΩ 上拉；防水探头线色没有统一标准，要用万用表确认 |
| DS18B20 未就绪，但 GPIO4 空闲电平读到 1 | 读到 1 不能证明上拉正常，悬空的输入也可能读成 1。断电后量 GPIO4 排针 ↔ 3V3 排针，应稳定在约 4.7 kΩ；几百 kΩ 到 MΩ 且读数乱跳，就是上拉没接通。10-10 台架上就是面包板连接不通（电源轨断开、插错排），把探头和上拉电阻改用杜邦线直接插 ESP32 排针后恢复正常 |
| 帧里常驻 `WAVEFORM_UNSTABLE` | 设 `DIAG = True` 看 `u_pos_v`/`u_neg_v`：两者同号，说明采样电阻下端接了 GND 而不是 VB |
| U 或 I 为负 | 接线方向反了，对调 `U_CH`/`I_CH` 的正负端（v2 标 `POLARITY`，原项目标 `COMPUTE_INVALID`） |
| v2 / `capture_serial.py` 打不开串口 | Thonny 是否还连着设备；当前用户是否在 dialout 组 |
| v2 一直停在「未连接」，提示停在「正在重启固件」 | `main.py` 是否已存到设备上（Thonny 文件面板里「MicroPython 设备」下应有 `main.py`）；仍没有输出就按一下板上 EN 键 |
| v2 一直「未连接」，提示是 `ERROR …` 日志 | 固件自检失败，按日志提示和上面几行排查 |
| 运行或导入时报 `MemoryError` | `main.py` 约 30 KB，要在板上现场编译，无 PSRAM 的板内存偏紧时可能失败。改为预编译：把 `main.py` 改名为 `ec_iv.py`，用与板上 MicroPython 同版本的 `mpy-cross` 编译成 `ec_iv.mpy` 并上传，再新建一个 `main.py`，只写两行：`import ec_iv` 和 `ec_iv.run()` |

## 9. 台架探测脚本 `bench/`

`bench/` 放的是单器件探测脚本，用 Thonny 打开后按 F5 直接在 ESP32 上跑，不经 `main.py`。它们来自树莓派上的旧版程序，2026-10-10 经过审查（[`bench/审查报告.md`](bench/审查报告.md)）后修正；在实物上的运行结果还没核实。

| 脚本 | 作用 | 10-10 修正 |
|---|---|---|
| `temptest.py` | DS18B20（GPIO4）每秒打印温度 | 等满 750 ms 再读（原来首个读数是 85 °C 上电值、之后滞后一拍，F7）；读失败不再退出 |
| `ads1256_probe.py` | ADS1256 三步探测：STATUS/ID=3 → 寄存器写读 → 自校准 + 读 AIN0 | 负电压按补码解码（原来显示成约 +10 V，F3）；写读测试改用 ADCON 0x00（原 0x08 会打开 0.5 µA 检测电流，F8） |
| `mcp4728_test.py` | MCP4728（SoftI2C，SDA=8/SCL=9，100 kHz）四路按码值输出，打印理论电压 | 退出时四路清零（原来停止后 A 通道一直保持 0.825 V，F4）。只接电阻负载，不要接溶液里的电极 |

原来的两个直流测量脚本 `electrochem.py`、`electrode_measure.py` 已退役（D4），要看原文可查 git 历史（提交 85378e5）。退役原因是直流两电极法测不了溶液电导率（审查报告 F1），项目路线文档也明令禁止用未验证的直流长期激励代替交流测量。溶液测量一律用 `main.py` 的方波法。

`main.py` 的 REPL 工具也能做器件探测：`main.probe()`、`main.volts()`、`main.dc()`。但在采样电阻改接 VB 之前，不要在接着溶液电极的回路上用它们（见第 1 节警告）。
