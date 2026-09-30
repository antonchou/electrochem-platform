import asyncio
import json

import pytest

from ec import devices as devices_module
from ec.chemistry import derive, kcl_kappa25_us_cm
from ec.devices import DeviceStatus, ReplayDevice, SerialDevice, SimulatedCell
from ec.frames import DEVICE_RESTART, SEQ_GAP, Reading


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def sim(**kwargs):
    clock = Clock()
    return SimulatedCell(seed=7, clock=clock, **kwargs), clock


def kappa25(r: Reading, kcell=1.02, alpha=0.0195):
    return derive(r.voltage_v, r.current_a, r.temperature_c, kcell, alpha).kappa25_us_cm


def test_simulated_readings_follow_the_physics():
    cell, clock = sim()
    cell.prepare("KCl", 10.0)
    clock.now = 30.0  # 远超趋稳时间常数
    r = cell.sample(30.0)
    # 用真 Kcell / 真 α 算回去，应得到溶液的 κ25（KCl 模型 + 水本底）
    assert kappa25(r) == pytest.approx(kcl_kappa25_us_cm(10.0) + 1.5, rel=2e-3)
    assert r.flags == ()
    assert r.temperature_c % 0.0625 == pytest.approx(0.0)  # DS18B20 分辨率
    assert r.info.device_id == "SIM-IV-01"


def test_uncalibrated_reading_is_off_by_the_true_cell_constant():
    cell, clock = sim(cell_constant_per_cm=1.05)
    clock.now = 30.0
    r = cell.sample(30.0)
    assert kappa25(r, kcell=1.0) == pytest.approx(kappa25(r, kcell=1.05) / 1.05, rel=1e-9)


def test_solution_change_settles_exponentially():
    cell, clock = sim(settle_s=2.0)
    cell.set_solution(100.0)
    assert cell.solution_kappa25(0.0) == pytest.approx(1413.0)
    assert cell.solution_kappa25(2.0) == pytest.approx(100 + 1313 * 0.3679, rel=1e-3)
    assert cell.solution_kappa25(30.0) == pytest.approx(100.0, rel=1e-4)


def test_prepare_without_concentration_keeps_the_solution():
    cell, _ = sim()
    cell.prepare("unknown", None)
    assert cell.solution_kappa25(100.0) == pytest.approx(1413.0)


@pytest.mark.parametrize(
    ("fault", "check"),
    [
        ("air", lambda r: "OPEN_CIRCUIT" in r.flags),
        ("no_temp", lambda r: r.temperature_c is None and "TEMP_INVALID" in r.flags),
        ("reversed", lambda r: r.voltage_v < 0 and derive(r.voltage_v, r.current_a, 25.0, 1.0, 0.02).polarity_error),
    ],
)
def test_faults(fault, check):
    cell, _ = sim()
    cell.set_fault(fault)
    assert check(cell.sample(10.0))


def test_dropout_every_fourth_frame():
    cell, _ = sim()
    cell.set_fault("dropout")
    frames = [cell.sample(i) for i in range(8)]
    assert [("DROPOUT" in r.flags) for r in frames] == [False, False, False, True] * 2
    assert frames[3].voltage_v is None and frames[3].temperature_c is not None


def test_very_high_conductivity_trips_short_circuit_like_the_firmware():
    cell, _ = sim()
    cell.set_solution(200_000.0)
    assert "SHORT_CIRCUIT" in cell.sample(60.0).flags


def test_unknown_fault_rejected():
    cell, _ = sim()
    with pytest.raises(ValueError):
        cell.set_fault("melted")


def test_simulator_stream_paces_itself():
    async def scenario():
        cell = SimulatedCell(rate_hz=50.0, seed=1)
        events = []
        loop = asyncio.get_running_loop()
        start = loop.time()
        async for event in cell.stream():
            events.append(event)
            if len(events) == 11:
                break
        return events, loop.time() - start

    events, elapsed = asyncio.run(scenario())
    assert events[0] == DeviceStatus(True, "模拟导电池已就绪")
    assert [e.device_seq for e in events[1:]] == list(range(1, 11))
    assert 0.15 < elapsed < 1.0  # 50 Hz × 9 个间隔 ≈ 0.18 s


def firmware_line(seq, ms, u=0.1, i=1e-4, t=25.0, flags=None):
    return json.dumps({
        "schema_version": 2, "seq_no": seq, "monotonic_ms": ms, "voltage_raw_v": u, "current_raw_a": i,
        "temperature_raw_c": t, "quality_flags": flags, "device_id": "ESP32-IV-TEST",
    })


def collect(stream, limit=50):
    async def run():
        out = []
        async for event in stream:
            out.append(event)
            if len(out) >= limit:
                break
        return out

    return asyncio.run(run())


def test_replay_device_replays_capture_file(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_text(
        "\n".join([
            "# INFO boot",
            firmware_line(1, 0),
            "garbage",
            firmware_line(2, 1000),
            firmware_line(4, 3000, flags="WAVEFORM_UNSTABLE"),
            firmware_line(1, 0),  # 设备重启
        ]) + "\n",
        encoding="utf-8",
    )
    events = collect(ReplayDevice(path, speed=1000.0).stream())
    assert events[0].connected and "4 帧" in events[0].message
    readings = [e for e in events if isinstance(e, Reading)]
    assert [r.device_seq for r in readings] == [1, 2, 4, 1]
    assert readings[2].flags == ("WAVEFORM_UNSTABLE", SEQ_GAP)
    assert readings[3].flags == (DEVICE_RESTART,)
    assert events[-1] == DeviceStatus(False, "回放结束")


def test_replay_loop_and_missing_file(tmp_path):
    path = tmp_path / "one.jsonl"
    path.write_text(firmware_line(1, 0) + "\n" + firmware_line(2, 1000) + "\n", encoding="utf-8")
    events = collect(ReplayDevice(path, speed=1000.0, loop=True).stream(), limit=6)
    assert [e.device_seq for e in events[1:]] == [1, 2, 1, 2, 1]
    missing = collect(ReplayDevice(tmp_path / "nope.jsonl").stream())
    assert len(missing) == 1 and not missing[0].connected


class FakePort:
    def __init__(self, lines):
        self.lines = [line.encode() for line in lines]
        self.closed = False

    def readline(self):
        if not self.lines:
            raise OSError("device unplugged")
        return self.lines.pop(0)

    def close(self):
        self.closed = True


def test_serial_device_parses_frames_logs_and_reconnects(monkeypatch):
    monkeypatch.setattr(devices_module, "RETRY_S", 0.01)
    ports = [FakePort(["# INFO ADS1256 正常\n", b"".decode(), firmware_line(1, 0) + "\n", firmware_line(3, 2000) + "\n"])]
    attempts = []

    def open_port():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("busy")
        return ports.pop(0) if ports else FakePort([])

    events = collect(SerialDevice("/dev/ttyUSB0", open_port=open_port).stream(), limit=7)
    assert events[0] == DeviceStatus(False, "打不开串口 /dev/ttyUSB0：busy")
    assert events[1] == DeviceStatus(True, "串口 /dev/ttyUSB0 已打开")
    assert events[2] == DeviceStatus(True, "INFO ADS1256 正常")
    assert [e.device_seq for e in events[3:5]] == [1, 3]
    assert events[4].flags == (SEQ_GAP,)
    assert events[5] == DeviceStatus(False, "串口断开：device unplugged")
    assert events[6] == DeviceStatus(True, "串口 /dev/ttyUSB0 已打开")  # 重连后序号检查重新开始


def test_sample_capture_goes_through_the_whole_chain():
    """v2/samples 里的采集文件：电极先在空气中（开路），放进 KCl 10 mM 后趋稳；尾部判稳应通过。"""
    from pathlib import Path

    from ec.qc import QcConfig, QcPoint, assess

    path = Path(__file__).resolve().parents[2] / "samples" / "simulated_kcl_10mM.jsonl"
    readings = ReplayDevice(path).load()
    assert len(readings) == 90
    assert all("OPEN_CIRCUIT" in r.flags for r in readings[:5])
    points = [
        QcPoint(float(i), derive(r.voltage_v, r.current_a, r.temperature_c, 1.0, 0.02).kappa25_us_cm, r.temperature_c, r.flags)
        for i, r in enumerate(readings)
    ]
    assert assess(points[:25], QcConfig()).verdict == "FAIL"  # 前段含开路与趋稳
    tail = assess(points, QcConfig())
    assert tail.verdict == "PASS", tail.reasons
    # 标称 Kcell = 1 下读数 = 真值 / 模拟器真 Kcell 1.02
    assert tail.representative_kappa25 == pytest.approx((kcl_kappa25_us_cm(10) + 1.5) / 1.02, rel=0.003)
