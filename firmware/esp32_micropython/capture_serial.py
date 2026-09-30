#!/usr/bin/env python3
"""ESP32 I–V 台架串口采集（树莓派上运行，只依赖 pyserial：sudo apt install python3-serial）。

把 main.py 输出的 JSON 帧落成三份文件：
  <out>.jsonl  设备帧逐行原样保存，另加主机接收时刻 timestamp_utc——全字段、可追溯
  <out>.csv    time_s,voltage_v,current,temperature_c,seq_no,monotonic_ms,quality_flags
               前 4 列即 EC_DRIVER=csv 回放格式（docs/接入数据格式.md §8），后 3 列供人工追溯
  <out>.log    设备日志行（"# " 开头）与串口杂讯

用法：
  python3 capture_serial.py --out ~/runs/kcl_1413_a                 # 采到 Ctrl-C
  python3 capture_serial.py --out ~/runs/r1k --seconds 600          # 采 10 分钟
  python3 capture_serial.py --input thonny_shell.txt --out ~/runs/x # 转换已保存的输出文本

采集前先在 Thonny 里断开设备（Run → Disconnect 或关掉 Thonny），否则串口被占用。
打开串口后脚本会让板子软重启一次（Ctrl-C、Ctrl-B、Ctrl-D），main.py 从器件自检重新开始跑——
Thonny 连接时会中断 main.py，断开后板子停在 REPL，不这样做就收不到帧。v2 的串口设备做法相同。
退出码：0 = 至少 1 行完整 U/I/T；1 = 没有完整帧；2 = 串口/参数错误或输出文件已存在（不覆盖原始数据）。
结束时 stdout 打印一行 JSON 汇总。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CSV_FIELDS = ("time_s", "voltage_v", "current", "temperature_c", "seq_no", "monotonic_ms", "quality_flags")
MAX_LINE_BYTES = 4096  # 固件一帧约 300 字节；攒到这么长还没换行，就当杂讯整段记进 .log
# Ctrl-C 停掉在跑的程序（固件先让电池回 0 V）、Ctrl-B 退出 raw REPL、Ctrl-D 软重启并运行 main.py。
# MicroPython 只在普通 REPL 下软重启才运行 main.py，所以 Ctrl-B 不能省。
RESTART_FIRMWARE = b"\x03\x02\x04"


class LineJoiner:
    """把 ser.readline() 的返回拼成完整行。

    pyserial 的 timeout 管的是整次 readline：一帧恰好在超时边界到达时，先返回前半行、下一次才返回后半行。
    固件 1 Hz 出帧、串口 timeout=1 s，这种相位会反复出现；逐次直接解析会把这一帧拆成两段杂讯丢掉。
    """

    def __init__(self) -> None:
        self.pending = b""

    def feed(self, raw: bytes) -> bytes | None:
        """喂入一次 readline 的返回；攒齐换行（或超长）时返回整行，否则返回 None。"""
        self.pending += raw
        if self.pending.endswith(b"\n") or len(self.pending) > MAX_LINE_BYTES:
            line, self.pending = self.pending, b""
            return line
        return None


def parse_line(line: str) -> tuple[str | None, object]:
    """→ ("frame", dict) | ("log", str) | ("noise", str) | (None, None)（空行）。"""
    s = line.strip()
    if not s:
        return None, None
    if s.startswith("{"):
        try:
            obj = json.loads(s)
        except ValueError:
            return "noise", s
        if isinstance(obj, dict) and "seq_no" in obj and "voltage_raw_v" in obj:
            return "frame", obj
        return "noise", s
    if s.startswith("#"):
        return "log", s
    return "noise", s


def _num(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


class Converter:
    """设备帧 → CSV 行。time_s 取设备 monotonic_ms 相对首帧的秒数；设备重启（计数回退）时接续，不倒流。"""

    def __init__(self) -> None:
        self.base_ms: int | None = None
        self.offset_s = 0.0
        self.last_ms: int | None = None
        self.last_seq: int | None = None
        self.last_time_s = 0.0
        self.last_host: float | None = None
        self.frames = 0
        self.complete = 0
        self.seq_gaps = 0
        self.restarts = 0
        self.flags: dict[str, int] = {}

    def feed(self, frame: dict, host_mono: float) -> dict:
        ms = frame.get("monotonic_ms")
        seq = frame.get("seq_no")
        if not isinstance(ms, int) or not isinstance(seq, int):
            raise ValueError("frame lacks integer monotonic_ms/seq_no")
        if self.base_ms is None:
            self.base_ms = ms
        elif ms < self.last_ms or seq <= self.last_seq:
            # 设备重启：新段接在上一帧之后，间隔取主机侧实际经过的时间
            self.restarts += 1
            self.offset_s = self.last_time_s + max(host_mono - self.last_host, 0.001)
            self.base_ms = ms
        elif seq > self.last_seq + 1:
            self.seq_gaps += seq - self.last_seq - 1
        time_s = self.offset_s + (ms - self.base_ms) / 1000.0
        self.last_ms, self.last_seq, self.last_time_s, self.last_host = ms, seq, time_s, host_mono
        self.frames += 1

        flags = frame.get("quality_flags") or ""
        for token in str(flags).split("|"):
            if token:
                self.flags[token] = self.flags.get(token, 0) + 1
        u = _num(frame.get("voltage_raw_v"))
        i = _num(frame.get("current_raw_a"))
        t = _num(frame.get("temperature_raw_c"))
        if u is not None and i is not None and t is not None:
            self.complete += 1
        # 缺值留空：CsvPlaybackDriver 解析失败会跳过该行，与后端「不完整读数不落库」一致
        return {
            "time_s": round(time_s, 3),
            "voltage_v": "" if u is None else u,
            "current": "" if i is None else i,
            "temperature_c": "" if t is None else t,
            "seq_no": seq,
            "monotonic_ms": ms,
            "quality_flags": flags,
        }

    def summary(self) -> dict:
        return {
            "frames": self.frames,
            "complete_rows": self.complete,
            "seq_gaps": self.seq_gaps,
            "device_restarts": self.restarts,
            "flags": self.flags,
        }


def _open_serial(port: str, baud: int):
    try:
        import serial  # type: ignore[import-not-found]
    except ImportError:
        print("缺少 pyserial：sudo apt install python3-serial", file=sys.stderr)
        return None
    try:
        ser = serial.Serial(port, baud, timeout=1.0)
        # 让固件软重启：Thonny 断开后板子停在 REPL。重启前的残帧与之后的新帧由 Converter 按设备重启接续时间轴
        ser.write(RESTART_FIRMWARE)
        return ser
    except (serial.SerialException, OSError) as e:
        print(f"打不开串口 {port}：{e}（Thonny 是否还连着设备？）", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ESP32 I–V 台架串口采集 → JSONL + CSV（EC_DRIVER=csv 可回放）")
    ap.add_argument("--out", required=True, help="输出路径前缀，生成 <out>.jsonl / .csv / .log")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=0, help="采集时长，0 = 直到 Ctrl-C")
    ap.add_argument("--max-frames", type=int, default=0, help="收到这么多帧后停止，0 = 不限")
    ap.add_argument("--input", help="从文本文件读（- 为 stdin）而不是串口，用于转换已保存的输出")
    ap.add_argument("--quiet", action="store_true", help="不在 stderr 打印逐帧进度")
    args = ap.parse_args(argv)

    out = Path(args.out).expanduser()
    paths = {k: f"{out}.{k}" for k in ("jsonl", "csv", "log")}
    existing = [p for p in paths.values() if Path(p).exists()]
    if existing:
        print(f"输出文件已存在，不覆盖原始数据：{existing}（换一个 --out）", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    src = ser = None
    if args.input:
        try:
            src = sys.stdin if args.input == "-" else open(args.input, encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"打不开输入文件：{e}", file=sys.stderr)
            return 2
        lines = iter(src)
    else:
        ser = _open_serial(args.port, args.baud)
        if ser is None:
            return 2
        lines = None

    conv = Converter()
    joiner = LineJoiner()
    started = time.monotonic()
    deadline = started + args.seconds if args.seconds > 0 else None
    with open(paths["jsonl"], "w", encoding="utf-8") as fj, \
            open(paths["csv"], "w", encoding="utf-8", newline="") as fc, \
            open(paths["log"], "w", encoding="utf-8") as fl:
        writer = csv.DictWriter(fc, fieldnames=CSV_FIELDS)
        writer.writeheader()
        try:
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                if ser is not None:
                    raw = ser.readline()
                    if not raw:
                        continue
                    joined = joiner.feed(raw)
                    if joined is None:
                        continue  # 读超时只拿到半行：等下一次 readline 补齐
                    line = joined.decode("utf-8", errors="replace")
                else:
                    line = next(lines, None)
                    if line is None:
                        break
                host_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                kind, payload = parse_line(line)
                if kind == "frame":
                    row = conv.feed(payload, time.monotonic())
                    fj.write(json.dumps({**payload, "timestamp_utc": host_utc}, ensure_ascii=False) + "\n")
                    writer.writerow(row)
                    fj.flush()
                    fc.flush()
                    if not args.quiet:
                        print(f"seq={row['seq_no']} U={row['voltage_v']} I={row['current']} "
                              f"T={row['temperature_c']} flags={row['quality_flags'] or '-'}", file=sys.stderr)
                    if args.max_frames and conv.frames >= args.max_frames:
                        break
                elif kind is not None:
                    fl.write(f"{host_utc} {payload}\n")
                    fl.flush()
                    if kind == "log" and not args.quiet:
                        print(payload, file=sys.stderr)
        except KeyboardInterrupt:
            pass
        finally:
            if joiner.pending:  # 结束时还没等到换行的残行：记进 .log，不丢
                rest = joiner.pending.decode("utf-8", errors="replace").strip()
                fl.write(f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')} [未完的行] {rest}\n")
            if ser is not None:
                ser.close()
            if src is not None and src is not sys.stdin:
                src.close()

    summary = conv.summary()
    summary["duration_s"] = round(time.monotonic() - started, 1)
    summary["files"] = paths
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if conv.complete > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
