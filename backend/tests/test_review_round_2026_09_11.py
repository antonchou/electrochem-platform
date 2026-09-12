"""2026-09-11 审查轮（任务清单 T-01~T-22）修复回归测试。

覆盖：
- T-01 stop()/flush() 竞态不再永久挂起；T-19 stop() 吞掉 writer 异常
- T-05 畸形帧（缺 NOT NULL 列）被跳过，不再整批失败触发持久化降级
- T-06 帧携带 calibration_id 时不再同时标 UNCALIBRATED
- T-07 export.json：404 语义与全量内容不回归
- T-14 y 恒定的退化数据不再产出 R²=1 的"完美拟合"
- T-15 _polyfit 大 x 量级数值稳定（中心化/缩放）
- T-16 CURRENT_ZERO 纳入判稳硬标志
- T-17 显式 dropout + dropout_every_n=0 不再变成隔帧丢弃
- T-18 全零时间戳 CSV 的 loop 回放不再病态取模
- T-20 state.reset() 还原 sample_id / sensor_path_id
- T-22 版本单一来源（__init__ 与 FastAPI app.version 一致）
"""

import asyncio

import pytest
from types import SimpleNamespace

from app import analysis, routes, storage
from app.drivers.csv_playback import CsvPlaybackConfig, CsvPlaybackDriver
from app.drivers.simulator import FaultKind, SimulatorConfig, SimulatorDriver, SimulatorMode
from app.persistence import PersistService, _FlushBarrier, _STOP
from app.stability import StabilityConfig, check_stability
from app.state import state


# ---------------- T-01 / T-19：persistence 停止竞态 ----------------

def test_flush_barrier_after_stop_marker_resolves(tmp_path, monkeypatch):
    """T-01：barrier 排在 _STOP 之后时，stop() 不得让 flush() 永久挂起。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t01.db"))

    async def scenario():
        service = PersistService()
        await service.start()
        # 复现竞态窗口：stop() 的 _STOP 先入队，随后并发调用的 flush() 才入队 barrier。
        # 修复前 drain 读到 _STOP 直接 return，barrier 永不 resolve。
        service._queue.put_nowait(_STOP)
        flush_task = asyncio.create_task(service.flush())
        await asyncio.sleep(0)  # 让 flush 先于 drain 执行、把 barrier 排进队列
        await asyncio.wait_for(service.stop(), timeout=1.0)
        await asyncio.wait_for(flush_task, timeout=1.0)

    asyncio.run(scenario())


def test_stop_resolves_barriers_enqueued_behind_stop_marker(tmp_path, monkeypatch):
    """T-01（验收口径）：barrier 在队列中位于 _STOP 之后，drain 退出前必须 settle。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t01b.db"))

    async def scenario():
        service = PersistService()
        await service.start()
        completed = asyncio.get_running_loop().create_future()
        service._queue.put_nowait(_STOP)
        service._queue.put_nowait(_FlushBarrier(completed))
        await asyncio.wait_for(service.stop(), timeout=1.0)
        assert completed.done()
        assert completed.exception() is None

    asyncio.run(scenario())


def test_stop_swallows_writer_exception(tmp_path, monkeypatch):
    """T-19：writer 带异常退出时 stop() 不向上抛，清理流程照常执行。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t19.db"))

    async def scenario():
        service = PersistService()
        await service.start()
        service._queue.put_nowait(object())  # 非法 item → writer 抛 TypeError 崩溃
        await asyncio.wait_for(service.stop(), timeout=1.0)  # 不得抛出
        assert service._task is None
        assert service._queue is None

    asyncio.run(scenario())


# ---------------- T-05：畸形帧不触发整批失败 ----------------

def test_insert_frames_skips_malformed_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t05.db"))
    storage.init_db()
    exp_id = storage.create_experiment("EXP-T05", "malformed rows")

    good = {
        "experiment_id": exp_id,
        "sample_id": "S1",
        "sensor_path_id": "MOCK_EC_IV",
        "t_seconds": 0.1,
        "ec_raw": 100.0,
        "temperature_raw": 25.0,
    }
    missing_exp = {k: v for k, v in good.items() if k != "experiment_id"}
    missing_temp = {k: v for k, v in good.items() if k != "temperature_raw"}

    # 修复前：KeyError / IntegrityError → 整批失败 → 持久化永久降级
    storage.insert_frames([good, missing_exp, missing_temp])

    frames = storage.get_frames(exp_id)
    assert len(frames) == 1
    assert frames[0]["t_seconds"] == 0.1
    samples = {s["sample_id"]: s["frame_count"] for s in storage.get_samples(exp_id)}
    assert samples == {"S1": 1}


def test_insert_frames_all_malformed_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t05b.db"))
    storage.init_db()
    storage.insert_frames([{"sample_id": "X", "t_seconds": 0.0}])  # 不抛异常即可


# ---------------- T-06 / P2-5：校准溯源一致性 ----------------

def test_calibration_claimed_follows_calibration_id_when_driver_silent(monkeypatch):
    """T-06：驱动未显式声明 claimed 时，有效校准 id 推导 claimed=True。"""
    cfg = SimpleNamespace(
        cell_constant_per_cm=1.0,
        alpha_per_c=0.02,
        calibration_id=None,
    )
    fake_driver = SimpleNamespace(config=cfg)
    monkeypatch.setattr(routes, "_driver", fake_driver)
    monkeypatch.setattr(state, "calibration_id", "ENV-CAL-01")
    try:
        params = routes._measurement_params()
    finally:
        monkeypatch.setattr(state, "calibration_id", None)
    assert params["calibration_id"] == "ENV-CAL-01"
    assert params["calibration_claimed"] is True


def test_calibration_claimed_explicit_false_wins(monkeypatch):
    """P2-5：驱动显式 calibration_claimed=False（有编号但未校准）不得被覆盖。"""
    cfg = SimpleNamespace(
        cell_constant_per_cm=1.0,
        alpha_per_c=0.02,
        calibration_id="SIM-KCELL-1.0",
        calibration_claimed=False,
    )
    fake_driver = SimpleNamespace(config=cfg)
    monkeypatch.setattr(routes, "_driver", fake_driver)
    monkeypatch.setattr(state, "calibration_id", "SIM-KCELL-1.0")
    try:
        params = routes._measurement_params()
    finally:
        monkeypatch.setattr(state, "calibration_id", None)
    assert params["calibration_claimed"] is False


# ---------------- T-07：export.json 语义 ----------------

def test_storage_export_json_full_content(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t07.db"))
    storage.init_db()
    exp_id = storage.create_experiment("EXP-T07", "export")
    storage.insert_frames(
        [
            {
                "experiment_id": exp_id,
                "sample_id": "S1",
                "sensor_path_id": "MOCK_EC_IV",
                "t_seconds": float(i),
                "ec_raw": 100.0 + i,
                "temperature_raw": 25.0,
            }
            for i in range(5)
        ]
    )
    import json

    payload = json.loads(storage.export_json(exp_id))
    assert payload["id"] == exp_id
    assert payload["experiment_id"] == "EXP-T07"
    assert payload["truncated"] is False
    assert payload["frame_count_total"] == 5
    assert len(payload["frames"]) == 5


def test_storage_export_json_missing_raises_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t07b.db"))
    storage.init_db()
    with pytest.raises(LookupError):
        storage.export_json(99999)


# ---------------- T-14：退化数据的 R² ----------------

def test_fit_all_constant_y_returns_no_results():
    y = [1413.0] * 20
    x = list(range(20))
    assert analysis.fit_all(x, y, ["linear", "quadratic"], "time") == []


def test_fit_linear_still_works_for_normal_data():
    res = analysis.fit_linear([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0], "time")
    assert res is not None
    assert abs(res["params"]["b"] - 2.0) < 1e-9
    assert res["r2"] > 0.9999


# ---------------- T-15：_polyfit 数值稳定性 ----------------

def test_polyfit_large_x_linear():
    # concentration 轴可达 1e4：修复前正规方程条件数爆炸/误判奇异
    x = [10_000.0 + 3.0 * i for i in range(30)]
    y = [1.5 * xi + 200.0 for xi in x]
    a, b = analysis._polyfit(x, y, 1)
    assert abs(b - 1.5) < 1e-6
    assert abs(a - 200.0) < 1e-3


def test_polyfit_large_x_quadratic():
    x = [800.0 + 0.7 * i for i in range(40)]
    y = [0.001 * xi**2 - 0.5 * xi + 10.0 for xi in x]
    a, b, c = analysis._polyfit(x, y, 2)
    assert abs(c - 0.001) < 1e-9
    assert abs(b + 0.5) < 1e-6
    assert abs(a - 10.0) < 1e-3


def test_fit_all_temperature_axis_large_values():
    # 温度轴 ~1e2 量级 + y 有真实斜率：linear 温补模型必须能解出
    x = [25.0 + 0.1 * i for i in range(50)]
    y = [1400.0 * (1.0 + 0.02 * (xi - 25.0)) for xi in x]
    results = analysis.fit_all(x, y, ["linear"], "temperature")
    assert results, "linear 温补模型在大 x 量级下不应被静默跳过"
    assert abs(results[0]["params"]["b"] - 28.0) < 1e-6


# ---------------- T-16：CURRENT_ZERO 硬标志 ----------------

def test_stability_current_zero_is_hard_flag():
    values = [1400.0 + 0.1 * i for i in range(30)]
    flags = [""] * 29 + ["SIMULATED|CURRENT_ZERO"]
    result = check_stability(values, quality_flags=flags, config=StabilityConfig())
    assert result.status == "FAIL"
    assert result.reason == "hard_quality_flag"


# ---------------- T-17：dropout_every_n=0 语义 ----------------

def test_simulator_dropout_zero_means_no_dropout():
    cfg = SimulatorConfig(
        mode=SimulatorMode.STABLE,
        fault_kind=FaultKind.DROPOUT,
        dropout_every_n=0,
        fault_start_s=0.0,
    )
    driver = SimulatorDriver(cfg)

    async def scenario():
        await driver.connect()
        dropped = 0
        for i in range(20):
            reading = await driver.read(i * 0.1)
            if "DROPOUT" in reading.quality_flags:
                dropped += 1
        return dropped

    assert asyncio.run(scenario()) == 0


# ---------------- T-18：全零时间戳 CSV 的 loop 回放 ----------------

def test_csv_playback_loop_degenerate_timestamps(tmp_path, monkeypatch):
    csv_path = tmp_path / "zero_t.csv"
    csv_path.write_text(
        "time_s,voltage_v,current,temperature_c\n"
        "0.0,0.5,0.001,25.0\n"
        "0.0,0.6,0.002,25.1\n",
        encoding="utf-8",
    )
    driver = CsvPlaybackDriver(CsvPlaybackConfig(path=str(csv_path), loop=True))

    async def scenario():
        await driver.connect()
        first = await driver.read(0.0)
        later = await driver.read(3600.0)  # 远超末尾时间戳：必须回绕到首行而非病态取模
        return first, later

    first, later = asyncio.run(scenario())
    assert "EOF" not in first.quality_flags
    assert "EOF" not in later.quality_flags
    assert later.voltage_v == first.voltage_v  # 固定回放首行
    assert later.temperature == first.temperature


# ---------------- T-20 / T-22：状态与版本 ----------------

def test_state_reset_restores_sample_and_path():
    async def scenario():
        await state.start(sample_id="CUSTOM", sensor_path_id="PATH_X", experiment_db_id=1)
        await state.reset()
        assert state.sample_id == "SAMPLE"
        assert state.sensor_path_id == "MOCK_EC_IV"
        assert state.experiment_db_id is None

    asyncio.run(scenario())


def test_version_single_source():
    from app import __version__
    from app.main import app

    assert app.version == __version__
