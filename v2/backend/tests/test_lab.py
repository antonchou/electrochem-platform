import asyncio
import json
import sqlite3
import time

import pytest

from conftest import INFO, Clock, FakeDevice, reading
from ec import lab as lab_module
from ec.devices import DeviceStatus
from ec.errors import Conflict, Invalid, Unavailable
from ec.frames import DeviceInfo, Reading
from ec.hub import Hub
from ec.lab import Lab
from ec.store import Store


@pytest.fixture(autouse=True)
def no_background_flush(monkeypatch):
    """后台每秒落库一次会与用例抢时机；这里的用例都显式触发落库（停止、_flush、关停）。"""
    monkeypatch.setattr(lab_module, "FLUSH_INTERVAL_S", 3600.0)


def run(coro):
    return asyncio.run(coro)


async def open_lab(settings, device=None, clock=None):
    lab = Lab(settings, Store(settings.db_path), device or FakeDevice(), clock or Clock())
    await lab.start()
    return lab


def drain(queue):
    return [json.loads(queue.get_nowait()) for _ in range(queue.qsize())]


async def record(lab, device, clock, n, **kwargs):
    for _ in range(n):
        clock.now += 1.0
        await device.send(reading(**kwargs))


def test_monitor_readings_are_pushed_but_not_stored(settings):
    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        queue = lab.hub.subscribe()
        await device.connect()
        await record(lab, device, clock, 3)
        messages = drain(queue)
        await lab.close()
        return lab, messages

    lab, messages = run(scenario())
    assert messages[0]["type"] == "snapshot" and not messages[0]["state"]["device"]["connected"]
    readings = [m for m in messages if m["type"] == "reading"]
    assert len(readings) == 3
    assert all(m["measurement_id"] is None for m in readings)
    assert readings[0]["point"]["kappa25_us_cm"] == pytest.approx(1000.0)  # 标称 Kcell = 1
    assert lab.state()["device"]["info"]["device_id"] == INFO.device_id
    assert lab.store.list_measurements() == []


def test_start_requires_a_connected_device(settings):
    async def scenario():
        lab = await open_lab(settings)
        try:
            with pytest.raises(Unavailable):
                await lab.start_measurement("x", None, None)
        finally:
            await lab.close()

    run(scenario())


def test_full_measurement_is_recorded_assessed_and_finished(settings):
    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        await device.connect()
        await record(lab, device, clock, 1)  # 带来设备元数据
        queue = lab.hub.subscribe()
        started = await lab.start_measurement("KCl", 10.0, "first")
        await record(lab, device, clock, 10)
        finished = await lab.stop_measurement()
        messages = drain(queue)
        frames = lab.store.frames(started["id"])
        await lab.close()
        return device, started, finished, messages, frames

    device, started, finished, messages, frames = run(scenario())
    assert device.prepared == [("KCl", 10.0)]
    assert started["device_id"] == INFO.device_id and started["cell_constant_per_cm"] == 1.0
    assert started["calibration_id"] is None
    assert [f.seq for f in frames] == list(range(1, 11))
    assert [f.t_s for f in frames] == [float(i) for i in range(1, 11)]
    assert finished["status"] == "completed"
    assert finished["qc"]["verdict"] == "PASS"
    assert finished["qc"]["representative_kappa25"] == pytest.approx(1000.0)
    assert finished["qc"]["config"]["window_s"] == settings.qc.window_s  # 阈值随测量存档
    recorded = [m for m in messages if m["type"] == "reading"]
    assert all(m["measurement_id"] == started["id"] for m in recorded)
    assert recorded[-1]["qc"]["verdict"] == "PASS"  # 实时判稳
    states = [m["state"] for m in messages if m["type"] == "state"]
    assert states[0]["measurement"]["id"] == started["id"]
    assert states[-1]["measurement"] is None and states[-1]["last_finished"]["id"] == started["id"]


def test_control_conflicts(settings):
    async def scenario():
        device = FakeDevice()
        lab = await open_lab(settings, device)
        await device.connect()
        with pytest.raises(Conflict):
            await lab.stop_measurement()
        await lab.start_measurement("a", None, None)
        with pytest.raises(Conflict):
            await lab.start_measurement("b", None, None)
        await lab.close()

    run(scenario())


def test_storage_failure_keeps_frames_and_recovers(settings):
    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        await device.connect()
        m = await lab.start_measurement("a", None, None)
        real_insert = lab.store.insert_frames

        def broken(rows):
            raise OSError("disk full")

        lab.store.insert_frames = broken
        await record(lab, device, clock, 3)
        with pytest.raises(OSError):
            await lab._flush()
        error = lab.state()["storage_error"]
        await record(lab, device, clock, 2)
        lab.store.insert_frames = real_insert
        await lab._flush()
        seqs = [f.seq for f in lab.store.frames(m["id"])]
        cleared = lab.state()["storage_error"]
        await lab.close()
        return error, seqs, cleared

    error, seqs, cleared = run(scenario())
    assert "3 帧待重试" in error and "disk full" in error
    assert seqs == [1, 2, 3, 4, 5]
    assert cleared is None


def test_stop_while_database_is_down_keeps_recording(settings):
    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        await device.connect()
        m = await lab.start_measurement("a", None, None)
        await record(lab, device, clock, 2)
        real_insert = lab.store.insert_frames
        lab.store.insert_frames = lambda rows: (_ for _ in ()).throw(OSError("locked"))
        with pytest.raises(Unavailable):
            await lab.stop_measurement()
        still_running = lab.measurement is not None
        await record(lab, device, clock, 1)
        lab.store.insert_frames = real_insert
        finished = await lab.stop_measurement()
        await lab.close()
        return still_running, finished, lab.store.frames(m["id"])

    still_running, finished, frames = run(scenario())
    assert still_running
    assert finished["status"] == "completed"
    assert [f.seq for f in frames] == [1, 2, 3]


def test_close_aborts_a_running_measurement_and_restart_cleans_up(settings):
    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        await device.connect()
        m = await lab.start_measurement("a", None, None)
        await record(lab, device, clock, 2)
        await lab.close()
        return lab.store.get_measurement(m["id"])

    m = run(scenario())
    assert m["status"] == "aborted" and m["frame_count"] == 2 and m["qc"] is None

    # 进程被杀、来不及 close：下次启动时把遗留的 running 标为 aborted
    store = Store(settings.db_path)
    orphan = store.create_measurement({**{k: m[k] for k in (
        "sample_name", "concentration_mmol_l", "note", "started_at", "cell_constant_per_cm", "calibration_id",
        "alpha_per_c", "device_kind", "device_id", "firmware_version", "range_id", "excitation_frequency_hz",
        "excitation_amplitude_v")}})

    async def restart():
        lab = await open_lab(settings)
        await lab.close()

    run(restart())
    assert store.get_measurement(orphan)["status"] == "aborted"


def test_calibration_changes_the_cell_constant_of_later_measurements(settings):
    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        await device.connect()
        standard = await lab.start_measurement("KCl 0.01", 10.0, None)
        # 标称 Kcell = 1 下读出 1385 µS/cm；标准液标称 1413 → Kcell = 1413 / 1385
        await record(lab, device, clock, 8, current_a=1.385e-4)
        await lab.stop_measurement()
        calibration = await lab.calibrate(
            [{"measurement_id": standard["id"], "standard_name": "KCl 0.01 mol/L", "standard_kappa25_us_cm": 1413.0}],
            {"operator": "tester", "lot": "L42"},
        )
        sample = await lab.start_measurement("tap water", None, None)
        with pytest.raises(Conflict):
            await lab.calibrate([], {})
        await record(lab, device, clock, 1, current_a=1.385e-4)
        live = lab.snapshot()["points"][-1]
        await lab.stop_measurement()
        await lab.close()
        return calibration, sample, live, lab.state()

    calibration, sample, live, state = run(scenario())
    assert calibration["cell_constant_per_cm"] == pytest.approx(1413 / 1385)
    assert calibration["operator"] == "tester" and calibration["points"][0]["standard_kappa25_us_cm"] == 1413.0
    assert sample["calibration_id"] == calibration["id"]
    assert sample["cell_constant_per_cm"] == pytest.approx(1413 / 1385)
    assert live["kappa25_us_cm"] == pytest.approx(1413.0)
    assert state["calibration"]["id"] == calibration["id"]


def test_calibration_needs_a_stable_completed_measurement(settings):
    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        await device.connect()
        m = await lab.start_measurement("KCl", None, None)
        await record(lab, device, clock, 8, flags=("OPEN_CIRCUIT",))
        await lab.stop_measurement()
        with pytest.raises(Invalid, match="判稳未通过"):
            await lab.calibrate(
                [{"measurement_id": m["id"], "standard_name": "x", "standard_kappa25_us_cm": 1413.0}], {}
            )
        await lab.close()

    run(scenario())


def test_snapshot_reflects_mode(settings):
    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        await device.connect()
        await record(lab, device, clock, 2)
        monitor = lab.snapshot()
        await lab.start_measurement("a", None, None)
        await record(lab, device, clock, 3)
        recording = lab.snapshot()
        await lab.close()
        return monitor, recording

    monitor, recording = run(scenario())
    assert len(monitor["points"]) == 2 and monitor["qc"] is None
    assert [p["seq"] for p in recording["points"]] == [1, 2, 3]
    assert recording["qc"]["n_points"] == 3


def test_device_errors_are_reported_and_the_stream_restarted(settings, monkeypatch):
    monkeypatch.setattr(lab_module, "DEVICE_RETRY_S", 0.01)

    class Flaky(FakeDevice):
        calls = 0

        async def stream(self):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise RuntimeError("usb glitch")
            async for event in super().stream():
                yield event

    async def scenario():
        device = Flaky()
        lab = await open_lab(settings, device)
        await asyncio.sleep(0.05)
        message = lab.device_message
        await device.connect()
        await device.send(reading())
        connected = lab.device_connected
        await lab.close()
        return message, connected

    message, connected = run(scenario())
    assert message == "设备异常：usb glitch"
    assert connected


def test_new_device_info_updates_state(settings):
    async def scenario():
        device = FakeDevice()
        lab = await open_lab(settings, device)
        await device.send(DeviceStatus(True, "hello"))
        other = reading()
        await device.send(type(other)(0.1, 1e-4, 25.0, info=DeviceInfo(device_id="ESP32-IV-OTHER")))
        info = lab.state()["device"]
        await lab.close()
        return info

    info = run(scenario())
    assert info["info"]["device_id"] == "ESP32-IV-OTHER" and info["message"] == "hello"


def test_hub_resyncs_a_slow_client_with_a_snapshot():
    async def scenario():
        hub = Hub(lambda: {"type": "snapshot", "n": 42}, queue_size=2)
        queue = hub.subscribe()
        hub.publish({"type": "reading", "i": 1})
        hub.publish({"type": "reading", "i": 2})  # 满了：换成快照
        return drain(queue)

    assert run(scenario()) == [{"type": "snapshot", "n": 42}]


def test_timeline_follows_the_device_clock_and_falls_back_on_restart(settings):
    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        await device.connect()
        await lab.start_measurement("replay x10", None, None)
        for ms in (0, 1000, 2000, 0):  # 回放 10 倍速：主机只过 0.1 s，设备过 1 s；最后一帧设备重启
            clock.now += 0.1
            await device.send(Reading(0.1, 1e-4, 25.0, (), ms // 1000 + 1, ms, INFO))
        times = [p["t_s"] for p in lab.snapshot()["points"]]
        await lab.close()
        return times

    assert run(scenario()) == [0.1, 1.1, 2.1, 2.2]


def test_failed_stop_marks_the_gap_in_the_recording(settings):
    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        await device.connect()
        m = await lab.start_measurement("a", None, None)
        await record(lab, device, clock, 2)
        real_insert = lab.store.insert_frames
        monitored = lab._monitor_count

        def slow_failing_insert(rows):
            # 写库要等到这一帧按监视处理完才失败。不能立即抛：Python 3.13 起，线程在登记回调前就结束时
            # to_thread 会同步完成、不让出事件循环，这一帧就到不了「停止等待写库期间」
            deadline = time.monotonic() + 5.0
            while lab._monitor_count == monitored and time.monotonic() < deadline:
                time.sleep(0.001)
            raise OSError("locked")

        lab.store.insert_frames = slow_failing_insert
        device.queue.put_nowait(reading())  # 停止等待写库期间到达的一帧：只按监视推送，没被记录
        with pytest.raises(Unavailable):
            await lab.stop_measurement()
        await device.queue.join()
        lab.store.insert_frames = real_insert
        await record(lab, device, clock, 2)
        await lab.stop_measurement()
        await lab.close()
        return [f.flags for f in lab.store.frames(m["id"])]

    assert run(scenario()) == [None, None, "SEQ_GAP", None]


def test_failed_stop_without_missed_readings_leaves_no_gap_flag(settings):
    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        await device.connect()
        m = await lab.start_measurement("a", None, None)
        await record(lab, device, clock, 2)
        real_insert = lab.store.insert_frames
        lab.store.insert_frames = lambda rows: (_ for _ in ()).throw(OSError("locked"))
        with pytest.raises(Unavailable):
            await lab.stop_measurement()
        lab.store.insert_frames = real_insert
        await record(lab, device, clock, 1)
        await lab.stop_measurement()
        await lab.close()
        return [f.flags for f in lab.store.frames(m["id"])]

    assert run(scenario()) == [None, None, None]


def test_stop_survives_a_failure_after_the_frames_were_written(settings):
    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        await device.connect()
        m = await lab.start_measurement("a", None, None)
        await record(lab, device, clock, 6)
        real_finish = lab.store.finish_measurement

        def locked(*args):
            raise sqlite3.OperationalError("database is locked")

        lab.store.finish_measurement = locked
        with pytest.raises(Unavailable, match="database is locked"):
            await lab.stop_measurement()
        still_running = lab.measurement is not None and lab.store.get_measurement(m["id"])["status"] == "running"
        lab.store.finish_measurement = real_finish
        finished = await lab.stop_measurement()
        await lab.close()
        return still_running, finished

    still_running, finished = run(scenario())
    assert still_running
    assert finished["status"] == "completed" and finished["frame_count"] == 6


def test_a_processing_error_skips_one_reading_but_keeps_the_device(settings, monkeypatch):
    real_make_point = lab_module.records.make_point
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("bug in point assembly")
        return real_make_point(*args, **kwargs)

    monkeypatch.setattr(lab_module.records, "make_point", flaky)

    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        await device.connect()
        await record(lab, device, clock, 3)
        state = (lab.device_connected, lab.device_message, len(lab.snapshot()["points"]))
        await lab.close()
        return state

    assert run(scenario()) == (True, "fake ready", 2)


def test_repeated_measurements_of_one_standard_calibrate_fine(settings):
    async def scenario():
        device, clock = FakeDevice(), Clock()
        lab = await open_lab(settings, device, clock)
        await device.connect()
        ids = []
        for current in (1.385e-4, 1.3852e-4):  # 同一标准液装夹两次，读数几乎一样
            m = await lab.start_measurement("KCl 0.01", 10.0, None)
            await record(lab, device, clock, 8, current_a=current)
            await lab.stop_measurement()
            ids.append(m["id"])
        good = await lab.calibrate(
            [{"measurement_id": i, "standard_name": "KCl 0.01 mol/L", "standard_kappa25_us_cm": k}
             for i, k in zip(ids, (1413.0, 1412.0))],
            {},
        )
        with pytest.raises(Invalid, match="偏差"):
            # 把 1413 的测量当成 147 的标准液：两点对不上
            await lab.calibrate(
                [{"measurement_id": ids[0], "standard_name": "KCl 0.01 mol/L", "standard_kappa25_us_cm": 1413.0},
                 {"measurement_id": ids[1], "standard_name": "KCl 0.001 mol/L", "standard_kappa25_us_cm": 147.0}],
                {},
            )
        await lab.close()
        return good

    good = run(scenario())
    assert good["cell_constant_per_cm"] == pytest.approx(1.0198, rel=1e-3)
    assert good["rsd_pct"] < 0.1  # 两次装夹的 Kcell 重复性
    assert good["fit"]["r2"] < 0  # 这种情况下 R² 本身没有意义，旧判据会误拒
