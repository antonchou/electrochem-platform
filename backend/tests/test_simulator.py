"""Experiment Simulator: DeviceDriver stand-in that emits raw V/I/T.

Tests go through DriverReading + compute_chain and, for the API cases,
through start/WS/persist — not through simulator internals as the product.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.drivers import (
    FaultKind,
    SimulatorConfig,
    SimulatorDriver,
    SimulatorMode,
    load_simulator_config,
)
from app.measurement import compute_chain
from app.routes import _build_driver


def _ols_slope(x: list[float], y: list[float]) -> tuple[float, float]:
    n = len(x)
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xx = sum(v * v for v in x)
    sum_xy = sum(a * b for a, b in zip(x, y))
    denom = n * sum_xx - sum_x * sum_x
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    mean_y = sum_y / n
    ss_tot = sum((yi - mean_y) ** 2 for yi in y)
    ss_res = sum((yi - (slope * xi + intercept)) ** 2 for xi, yi in zip(x, y))
    r2 = 1.0 if ss_tot < 1e-30 else 1.0 - ss_res / ss_tot
    return slope, r2


async def _collect(driver: SimulatorDriver, times: list[float]):
    await driver.connect()
    try:
        return [await driver.read(t) for t in times]
    finally:
        await driver.close()


def test_simulator_starts_and_emits_iv_readings():
    cfg = SimulatorConfig(mode=SimulatorMode.STABLE, seed=7, sweep_seconds=2.0)

    async def scenario():
        readings = await _collect(SimulatorDriver(cfg), [0.0, 0.5, 1.5, 2.6])
        assert all(r.complete_for_iv for r in readings)
        assert all("SIMULATED" in r.quality_flags for r in readings)
        assert all(r.ec is None for r in readings)
        voltages = [r.voltage_v for r in readings]
        assert voltages[0] < voltages[2]
        assert voltages[-1] == pytest.approx(cfg.sweep_end_v, abs=0.02)

    asyncio.run(scenario())


def test_stable_iv_recovers_nominal_g():
    cfg = SimulatorConfig(
        mode=SimulatorMode.STABLE,
        seed=1,
        settle_seconds=0.0,
        sweep_seconds=2.0,
        voltage_noise_v=0.0,
        current_noise_a=0.0,
        temperature_noise=0.0,
    )

    async def scenario():
        driver = SimulatorDriver(cfg)
        readings = await _collect(driver, [i * 0.1 for i in range(21)])
        xs = [r.voltage_v for r in readings]
        ys = [r.current_a for r in readings]
        slope, r2 = _ols_slope(xs, ys)
        assert r2 > 0.999
        assert slope == pytest.approx(cfg.nominal_conductance_s, rel=0.01)
        chained = [
            compute_chain(
                r.voltage_v,
                r.current_a,
                r.temperature,
                cfg.cell_constant_per_cm,
                cfg.alpha_per_c,
            )
            for r in readings
        ]
        k25 = [c.kappa_25_us_cm for c in chained]
        expected = cfg.cell_constant_per_cm * cfg.nominal_conductance_s * 1e6
        assert sum(k25) / len(k25) == pytest.approx(expected, rel=0.02)

    asyncio.run(scenario())


def test_realistic_mode_still_recovers_g_with_noise():
    cfg = SimulatorConfig(mode=SimulatorMode.REALISTIC, seed=11, settle_seconds=0.0, sweep_seconds=3.0)

    async def scenario():
        readings = await _collect(SimulatorDriver(cfg), [i * 0.1 for i in range(31)])
        assert all(r.complete_for_iv for r in readings)
        slope, r2 = _ols_slope(
            [r.voltage_v for r in readings],
            [r.current_a for r in readings],
        )
        assert r2 > 0.9
        assert slope == pytest.approx(cfg.nominal_conductance_s, rel=0.15)

    asyncio.run(scenario())


def test_fault_dropout_returns_incomplete_reading():
    cfg = SimulatorConfig(
        mode=SimulatorMode.FAULT,
        fault_kind=FaultKind.DROPOUT,
        dropout_every_n=3,
        fault_start_s=0.0,
        seed=2,
    )

    async def scenario():
        readings = await _collect(SimulatorDriver(cfg), [0.0, 0.1, 0.2])
        assert readings[2].complete_for_iv is False
        assert "DROPOUT" in readings[2].quality_flags
        assert "SIMULATED" in readings[2].quality_flags

    asyncio.run(scenario())


def test_fault_voltage_oor_is_nonpositive():
    cfg = SimulatorConfig(
        mode=SimulatorMode.FAULT,
        fault_kind=FaultKind.VOLTAGE_OOR,
        fault_start_s=0.0,
        seed=3,
    )

    async def scenario():
        readings = await _collect(SimulatorDriver(cfg), [0.0, 0.1])
        bad = [r for r in readings if r.voltage_v is not None and r.voltage_v <= 0]
        assert bad
        with pytest.raises(ValueError, match="voltage_v"):
            compute_chain(
                bad[0].voltage_v,
                bad[0].current_a or 0.0,
                bad[0].temperature or 25.0,
                1.0,
                0.02,
            )

    asyncio.run(scenario())


def test_unknown_simulator_field_rejected():
    with pytest.raises(ValueError, match="unknown simulator config fields"):
        SimulatorConfig.from_mapping({"mode": "stable", "typo": 1})


def test_load_simulator_mode_override(monkeypatch, tmp_path):
    path = tmp_path / "sim.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "driver": "simulator",
                "mode": "stable",
                "nominal_conductance_s": 0.002,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EC_SIM_CONFIG", str(path))
    monkeypatch.setenv("EC_SIM_MODE", "realistic")
    cfg = load_simulator_config()
    assert cfg.mode is SimulatorMode.REALISTIC
    assert cfg.nominal_conductance_s == 0.002
    assert cfg.nonlinearity > 0


def test_build_driver_unknown_kind(monkeypatch):
    monkeypatch.setenv("EC_DRIVER", "ads1256")
    with pytest.raises(ValueError, match="unknown EC_DRIVER"):
        _build_driver()


def test_example_config_file_loads():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    cfg = SimulatorConfig.from_json_file(
        os.path.join(root, "configs", "devices", "simulator.example.json")
    )
    assert cfg.mode is SimulatorMode.STABLE
    assert cfg.solution_id.endswith("SIMULATED")


@pytest.fixture()
def sim_client(tmp_path, monkeypatch):
    path = tmp_path / "sim.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "driver": "simulator",
                "simulated": True,
                "mode": "stable",
                "seed": 4,
                "sample_rate_hz": 20,
                "nominal_conductance_s": 0.001413,
                "sweep_start_v": 0.2,
                "sweep_end_v": 1.0,
                "settle_seconds": 0.1,
                "sweep_seconds": 1.2,
                "device_id": "SIM-IV-01",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EC_DRIVER", "simulator")
    monkeypatch.setenv("EC_SIM_CONFIG", str(path))
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "sim.db"))
    monkeypatch.setenv("EC_ENABLE_DEBUG_ENDPOINTS", "1")
    from app.main import app

    with TestClient(app) as client:
        yield client
    for key in ("EC_DRIVER", "EC_SIM_CONFIG", "EC_DB_PATH", "EC_ENABLE_DEBUG_ENDPOINTS"):
        monkeypatch.delenv(key, raising=False)


def test_simulator_ws_schema_and_persist(sim_client):
    """Business path: start → WS frame (existing schema) → SQLite."""
    from app import storage

    with sim_client.websocket_connect("/ws/stream") as ws:
        start = sim_client.post("/api/experiment/start", json={"sample_id": "SIM_A"})
        assert start.json()["ok"] is True
        exp_id = start.json()["experiment_id"]
        data = None
        for _ in range(20):
            msg = ws.receive_json()
            if "voltage_raw_v" in msg:
                data = msg
                break
        assert data is not None
        assert data["status"] == "running"
        assert data["device_id"] == "SIM-IV-01"
        assert "SIMULATED" in (data.get("quality_flags") or "")
        assert data["voltage_raw_v"] > 0
        assert data["current_raw_a"] is not None
        assert data["kappa_25_us_cm"] is not None
        assert data["conductance_s"] is not None
        time.sleep(1.5)
        sim_client.post("/api/experiment/stop")
        sim_client.post("/api/experiment/reset")

    frames = storage.get_frames(exp_id, limit=200)
    assert len(frames) >= 10
    assert all("SIMULATED" in (f.get("quality_flags") or "") for f in frames)
    vs = [f["voltage_raw_v"] for f in frames if f.get("voltage_raw_v")]
    assert max(vs) - min(vs) > 0.05


def test_simulator_api_recovers_nominal_g(sim_client):
    """Nominal G vs OLS on persisted I–V (existing frames, no new analysis schema)."""
    from app import storage

    exp_id = sim_client.post("/api/experiment/start").json()["experiment_id"]
    time.sleep(1.6)
    sim_client.post("/api/experiment/stop")
    frames = [f for f in storage.get_frames(exp_id, limit=500) if f.get("voltage_raw_v") and f.get("current_raw_a")]
    assert len(frames) >= 8
    slope, r2 = _ols_slope(
        [f["voltage_raw_v"] for f in frames],
        [f["current_raw_a"] for f in frames],
    )
    assert r2 > 0.98
    assert slope == pytest.approx(0.001413, rel=0.05)
    rel_err = abs(slope - 0.001413) / 0.001413
    assert rel_err < 0.05


def test_fault_mode_does_not_crash_experiment(tmp_path, monkeypatch):
    path = tmp_path / "fault.json"
    path.write_text(
        json.dumps(
            {
                "driver": "simulator",
                "mode": "fault",
                "fault_kind": "voltage_oor",
                "fault_start_s": 0.0,
                "sample_rate_hz": 20,
                "sweep_seconds": 0.8,
                "settle_seconds": 0.05,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EC_DRIVER", "simulator")
    monkeypatch.setenv("EC_SIM_CONFIG", str(path))
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "fault.db"))
    from app.main import app

    with TestClient(app) as client:
        exp_id = client.post("/api/experiment/start").json()["experiment_id"]
        time.sleep(0.9)
        stop = client.post("/api/experiment/stop")
        assert stop.json()["ok"] is True
        from app import storage

        frames = storage.get_frames(exp_id, limit=200)
        flags = " ".join(f.get("quality_flags") or "" for f in frames)
        assert "SIMULATED" in flags
        assert "COMPUTE_INVALID" in flags or "VOLTAGE_OOR" in flags
