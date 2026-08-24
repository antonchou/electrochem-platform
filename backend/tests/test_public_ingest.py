"""Ingest public electrochemistry fixtures through CsvPlaybackDriver + compute_chain."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.drivers import CsvPlaybackConfig, CsvPlaybackDriver
from app.main import app
from app.measurement import compute_chain

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "data" / "fixtures" / "ingest"
BRAUN = FIXTURES / "braun_2022_lib_cell.csv"
RAHMANIAN = FIXTURES / "rahmanian_2022_eis_bm169.csv"
ECHEMDB = FIXTURES / "echemdb_hermann_2021_cv.csv"
RAHMANIAN_KCELL = 4.72026


def test_fixtures_exist():
    assert BRAUN.is_file()
    assert RAHMANIAN.is_file()
    assert ECHEMDB.is_file()


def test_braun_cell_ivt_all_positive_voltage():
    import asyncio
    import csv

    rows = list(csv.DictReader(BRAUN.open(encoding="utf-8")))
    assert len(rows) >= 100
    assert all(float(r["voltage_v"]) > 0 for r in rows)

    async def scenario():
        driver = CsvPlaybackDriver(CsvPlaybackConfig(path=str(BRAUN)))
        await driver.connect()
        reading = await driver.read(10.0)
        assert reading.complete_for_iv
        assert reading.voltage_v > 0
        result = compute_chain(
            reading.voltage_v,
            reading.current_a,
            reading.temperature,
            1.0,
            0.02,
        )
        assert result.conductance_s == pytest.approx(reading.current_a / reading.voltage_v)
        await driver.close()

    asyncio.run(scenario())


def test_rahmanian_reconstructed_kappa_matches_eis():
    import asyncio
    import csv

    rows = list(csv.DictReader(RAHMANIAN.open(encoding="utf-8")))
    assert len(rows) == 9

    async def scenario():
        driver = CsvPlaybackDriver(
            CsvPlaybackConfig(path=str(RAHMANIAN), cell_constant_per_cm=RAHMANIAN_KCELL)
        )
        await driver.connect()
        # t=4 s is 20 °C in the fixture
        reading = await driver.read(4.0)
        result = compute_chain(
            reading.voltage_v,
            reading.current_a,
            reading.temperature,
            RAHMANIAN_KCELL,
            0.02,
        )
        source = float(rows[4]["current"]) * RAHMANIAN_KCELL * 1e6  # μS/cm at T
        assert reading.temperature == pytest.approx(20.0)
        assert result.kappa_t_us_cm == pytest.approx(source, rel=1e-9)
        await driver.close()

    asyncio.run(scenario())


def test_echemdb_cv_marks_nonpositive_voltage_invalid():
    import asyncio

    async def scenario():
        driver = CsvPlaybackDriver(CsvPlaybackConfig(path=str(ECHEMDB)))
        await driver.connect()
        reading = await driver.read(0.0)
        assert reading.voltage_v is not None and reading.voltage_v < 0
        with pytest.raises(ValueError, match="voltage_v"):
            compute_chain(
                reading.voltage_v,
                reading.current_a,
                reading.temperature,
                1.0,
                0.02,
            )
        await driver.close()

    asyncio.run(scenario())


def test_api_braun_persist_computes_kappa(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "braun.db"))
    monkeypatch.setenv("EC_ENABLE_DEBUG_ENDPOINTS", "1")
    monkeypatch.setenv("EC_DRIVER", "csv")
    monkeypatch.setenv("EC_CSV_PATH", str(BRAUN))
    monkeypatch.setenv("EC_CSV_SAMPLE_RATE_HZ", "20")
    from app import storage

    with TestClient(app) as client:
        exp_id = client.post("/api/experiment/start").json()["experiment_id"]
        import time as _time

        _time.sleep(0.4)
        client.post("/api/experiment/stop")
        client.post("/api/experiment/reset")
        frames = storage.get_frames(exp_id, limit=200)
        assert len(frames) >= 3
        assert all(f["voltage_raw_v"] and f["voltage_raw_v"] > 0 for f in frames)
        assert any(f.get("kappa_25_us_cm") is not None for f in frames)
        temps = [f["temperature_raw"] for f in frames]
        assert min(temps) > 20
        assert max(temps) < 30


def test_api_echemdb_persist_raw_when_compute_invalid(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "cv.db"))
    monkeypatch.setenv("EC_ENABLE_DEBUG_ENDPOINTS", "1")
    monkeypatch.setenv("EC_DRIVER", "csv")
    monkeypatch.setenv("EC_CSV_PATH", str(ECHEMDB))
    monkeypatch.setenv("EC_CSV_SAMPLE_RATE_HZ", "50")
    from app import storage

    with TestClient(app) as client:
        exp_id = client.post("/api/experiment/start").json()["experiment_id"]
        import time as _time

        _time.sleep(0.3)
        client.post("/api/experiment/stop")
        client.post("/api/experiment/reset")
        frames = storage.get_frames(exp_id, limit=200)
        assert len(frames) >= 3
        invalid = [f for f in frames if "COMPUTE_INVALID" in (f.get("quality_flags") or "")]
        assert invalid, "CV start is E<0 so compute must be marked invalid"
        assert all(f["voltage_raw_v"] is not None for f in invalid)
        assert all(f.get("kappa_25_us_cm") is None for f in invalid)
