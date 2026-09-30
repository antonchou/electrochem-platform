import sqlite3

import pytest

from ec import store as store_module
from ec.store import SCHEMA_VERSION, FrameRow, Store, migrate


def params(**overrides):
    base = {
        "sample_name": "KCl 10 mM",
        "concentration_mmol_l": 10.0,
        "note": None,
        "started_at": "2026-10-01T00:00:00.000Z",
        "cell_constant_per_cm": 1.0,
        "calibration_id": None,
        "alpha_per_c": 0.02,
        "device_kind": "sim",
        "device_id": "SIM-IV-01",
        "firmware_version": "sim-2",
        "range_id": "RS1000R_G1",
        "excitation_frequency_hz": 10.0,
        "excitation_amplitude_v": 0.2,
    }
    return {**base, **overrides}


def frame(measurement_id, seq, **overrides):
    values = dict(
        measurement_id=measurement_id, seq=seq, t_s=seq * 0.5, timestamp_utc="2026-10-01T00:00:01.000Z",
        device_seq=seq, device_ms=seq * 500, voltage_v=0.1, current_a=1e-4, temperature_c=25.0, flags=None,
    )
    values.update(overrides)
    return FrameRow(**values)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ec.db")


def test_fresh_database_is_at_current_version(store):
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert Store(store.path).list_measurements() == []  # 重复打开是幂等的


def test_newer_database_is_refused(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="newer"):
        Store(path)


def test_failed_migration_rolls_back_completely(tmp_path, monkeypatch):
    broken = "CREATE TABLE half_done (x INTEGER); CREATE TABLE half_done (x INTEGER);"
    monkeypatch.setattr(store_module, "MIGRATIONS", (broken,))
    monkeypatch.setattr(store_module, "SCHEMA_VERSION", 1)
    conn = sqlite3.connect(tmp_path / "broken.db", isolation_level=None)
    with pytest.raises(sqlite3.Error):
        migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'half_done'").fetchall() == []


def test_measurement_lifecycle_and_listing(store):
    mid = store.create_measurement(params())
    store.insert_frames([frame(mid, 1), frame(mid, 2), frame(mid, 3, flags="TEMP_INVALID", temperature_c=None)])
    m = store.get_measurement(mid)
    assert m["status"] == "running" and m["frame_count"] == 3 and m["duration_s"] == 1.5
    store.finish_measurement(mid, "completed", "2026-10-01T00:01:00.000Z", {"verdict": "PASS"})
    m = store.get_measurement(mid)
    assert (m["status"], m["qc"]) == ("completed", {"verdict": "PASS"})
    assert [f.seq for f in store.frames(mid)] == [1, 2, 3]
    assert store.frames(mid)[2].temperature_c is None
    other = store.create_measurement(params(sample_name="blank", concentration_mmol_l=None))
    assert [m["id"] for m in store.list_measurements()] == [other, mid]
    assert store.list_measurements()[0]["frame_count"] == 0
    assert store.running_measurement_ids() == [other]


def test_frames_are_append_only(store):
    mid = store.create_measurement(params())
    store.insert_frames([frame(mid, 1)])
    with store._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE frames SET voltage_v = 1 WHERE seq = 1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM frames")


def test_duplicate_frame_is_rejected_and_batch_rolled_back(store):
    mid = store.create_measurement(params())
    store.insert_frames([frame(mid, 1)])
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_frames([frame(mid, 2), frame(mid, 1)])
    assert [f.seq for f in store.frames(mid)] == [1]


def test_finished_measurement_is_immutable(store):
    mid = store.create_measurement(params())
    store.finish_measurement(mid, "completed", "t", None)
    store.finish_measurement(mid, "aborted", "t2", None)  # 已结束：WHERE status='running' 不命中
    assert store.get_measurement(mid)["status"] == "completed"
    with store._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE measurements SET qc = '{}' WHERE id = ?", (mid,))
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute("DELETE FROM measurements")


def test_measurement_parameters_cannot_change_even_while_running(store):
    mid = store.create_measurement(params())
    with store._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE measurements SET cell_constant_per_cm = 2 WHERE id = ?", (mid,))


def test_calibrations(store):
    mid = store.create_measurement(params(sample_name="KCl 1413"))
    cal = {"created_at": "t", "cell_constant_per_cm": 1.02, "r2": None, "rsd_pct": None, "operator": "A", "cell_id": None,
           "lot": "L1", "note": None}
    point = {"measurement_id": mid, "standard_name": "KCl 0.01 mol/L", "standard_kappa25_us_cm": 1413.0,
             "conductance25_s": 1.385e-3, "deviation_pct": 0.0, "verdict": "PASS"}
    cid = store.create_calibration(cal, [point])
    latest = store.latest_calibration()
    assert latest["id"] == cid and latest["cell_constant_per_cm"] == 1.02
    assert latest["points"][0]["sample_name"] == "KCl 1413"
    with store._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE calibrations SET cell_constant_per_cm = 1")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_calibration({**cal, "cell_constant_per_cm": 0.0}, [])


def test_frames_tail_window(store):
    mid = store.create_measurement(params())
    store.insert_frames([frame(mid, seq) for seq in range(1, 21)])  # t_s = 0.5 … 10.0
    tail = store.frames(mid, last_seconds=2.0)
    assert [f.t_s for f in tail] == [8.0, 8.5, 9.0, 9.5, 10.0]
    assert store.frames(mid + 1, last_seconds=2.0) == []
