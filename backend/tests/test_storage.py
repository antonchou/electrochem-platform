"""SQLite 存储层测试：生命周期、append-only 约束、样品汇总、CSV 导出。"""

import os
import sqlite3

import pytest


@pytest.fixture()
def store(tmp_path):
    os.environ["EC_DB_PATH"] = str(tmp_path / "storage_test.db")
    from app import storage

    storage.init_db()
    yield storage
    os.environ.pop("EC_DB_PATH", None)


def test_experiment_lifecycle(store):
    eid = store.create_experiment_with_sample(
        "EXP-TEST-001",
        "不同溶液导电性相对比较",
        "NACL_004",
        "CM2_WIDE",
        metadata={"c_mmol_l": 4.0},
    )
    items = store.list_experiments()
    assert len(items) == 1
    assert items[0]["id"] == eid
    assert items[0]["status"] == "running"
    assert items[0]["sample_id"] == "NACL_004"

    store.finish_experiment(eid, "stopped")
    exp = store.get_experiment(eid)
    assert exp["status"] == "stopped"
    assert exp["ended_at_utc"] is not None


def test_abort_stale_running_experiments_leaves_terminal_rows(store):
    """P1-B：启动兜底只动 running 行，stopped/aborted 不动。"""
    leftover = store.create_experiment_with_sample("EXP-STALE", "leftover", "S", "WIDE")
    stopped = store.create_experiment_with_sample("EXP-STOPPED", "done", "S", "WIDE")
    store.finish_experiment(stopped, "stopped")
    aborted = store.create_experiment_with_sample("EXP-ABORTED", "killed", "S", "WIDE")
    store.finish_experiment(aborted, "aborted")

    assert store.abort_stale_running_experiments() == 1
    stale = store.get_experiment(leftover)
    assert stale["status"] == "aborted"
    assert stale["ended_at_utc"] is not None
    assert store.get_experiment(stopped)["status"] == "stopped"
    assert store.get_experiment(aborted)["status"] == "aborted"
    assert store.abort_stale_running_experiments() == 0


def test_raw_frames_append_only(store):
    """REQ-D-001：原始帧只追加，UPDATE/DELETE 均被拒绝。"""
    eid = store.create_experiment_with_sample("EXP-002", "t", "S", "CM2_WIDE")
    store.insert_frames(
        [
            {
                "experiment_id": eid,
                "sample_id": "S",
                "sensor_path_id": "CM2_WIDE",
                "seq_no": 1,
                "timestamp_utc": "2026-08-19T00:00:00Z",
                "monotonic_ms": 1000,
                "t_seconds": 0.1,
                "ec_raw": 1413.0,
                "temperature_raw": 25.0,
                "k25": None,
                "quality_flags": None,
                "status": "running",
            }
        ]
    )
    frames = store.get_frames(eid)
    assert len(frames) == 1

    # sqlite 对触发器 RAISE(ABORT) 的错误类型在不同版本可能是 OperationalError 或 IntegrityError
    with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError)):
        with store._conn() as conn:
            conn.execute("UPDATE raw_frames SET ec_raw = 999 WHERE experiment_id = ?", (eid,))
    with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError)):
        with store._conn() as conn:
            conn.execute("DELETE FROM raw_frames WHERE experiment_id = ?", (eid,))

    # 数据仍然完好
    assert store.count_frames(eid) == 1


def test_export_csv(store):
    eid = store.create_experiment_with_sample("EXP-004", "t", "NACL_006", "CM2_WIDE")
    store.insert_frames(
        [
            {
                "experiment_id": eid,
                "sample_id": "NACL_006",
                "sensor_path_id": "CM2_WIDE",
                "seq_no": i,
                "timestamp_utc": f"2026-08-19T00:00:{i:02d}Z",
                "monotonic_ms": i * 100,
                "t_seconds": i * 0.1,
                "ec_raw": 1413.0 + i,
                "temperature_raw": 25.0,
                "k25": None,
                "quality_flags": None,
                "status": "running",
            }
            for i in range(1, 4)
        ]
    )
    csv_text = store.export_csv(eid)
    lines = csv_text.strip().split("\n")
    assert len(lines) == 4  # 1 表头 + 3 数据行
    assert "sensor_path_id" in lines[0]
    assert "kappa_25_us_cm" in lines[0]
    assert "calibration_id" in lines[0]
    assert "NACL_006" in lines[1]


def test_export_csv_neutralizes_formula_injection(store):
    """sample_id 以 = + - @ 开头时导出必须前置单引号，防止 Excel 公式执行。"""
    eid = store.create_experiment_with_sample("EXP-INJ", "t", "=cmd|'/c calc'!A1", "CM2_WIDE")
    store.insert_frames(
        [
            {
                "experiment_id": eid,
                "sample_id": "=cmd|'/c calc'!A1",
                "sensor_path_id": "@evil",
                "seq_no": 1,
                "timestamp_utc": "2026-08-19T00:00:00Z",
                "monotonic_ms": 1000,
                "t_seconds": 0.1,
                "ec_raw": -1413.0,
                "temperature_raw": 25.0,
                "k25": None,
                "quality_flags": None,
                "status": "running",
            }
        ]
    )
    csv_text = store.export_csv(eid)
    data_line = csv_text.strip().split("\n")[1]
    assert "'=cmd" in data_line
    assert "'@evil" in data_line
    # 数值列不受消毒影响（负数仍是数字字面量，非 str）
    assert "-1413.0" in data_line


def test_get_recent_frames_returns_tail(store):
    eid = store.create_experiment_with_sample("EXP-TAIL", "t", "S", "MOCK_EC_IV")
    store.insert_frames(
        [
            {
                "experiment_id": eid,
                "sample_id": "S",
                "sensor_path_id": "MOCK_EC_IV",
                "seq_no": i,
                "timestamp_utc": f"2026-08-19T00:00:{i:02d}Z",
                "monotonic_ms": i * 100,
                "t_seconds": float(i),
                "ec_raw": float(i),
                "temperature_raw": 25.0,
                "k25": None,
                "quality_flags": None,
                "status": "running",
            }
            for i in range(1, 11)
        ]
    )
    tail = store.get_recent_frames(eid, limit=3)
    assert [row["seq_no"] for row in tail] == [8, 9, 10]


def test_calibration_and_frame_trace_columns(store):
    eid = store.create_experiment_with_sample("EXP-CAL", "t", "S", "MOCK_EC_IV")
    rid = store.insert_calibration_record(
        experiment_id=eid,
        calibration_id="MOCK-KCELL-1.0",
        sensor_path_id="MOCK_EC_IV",
        mode="cell_constant",
        standard="KCl 1413",
        lot="SIMULATED",
        coeff_value=1.0,
        coeff_json={"alpha_per_c": 0.02},
    )
    assert rid > 0
    store.insert_frames(
        [
            {
                "experiment_id": eid,
                "sample_id": "S",
                "sensor_path_id": "MOCK_EC_IV",
                "seq_no": 1,
                "timestamp_utc": "2026-08-19T00:00:00Z",
                "monotonic_ms": 1000,
                "t_seconds": 0.1,
                "ec_raw": 1413.0,
                "temperature_raw": 25.0,
                "k25": 1413.0,
                "quality_flags": "SIMULATED",
                "status": "running",
                "kappa_25_us_cm": 1413.0,
                "schema_version": 2,
                "device_id": "MOCK-IV-01",
                "firmware_version": "0.1.0",
                "range_id": "WIDE",
                "calibration_id": "MOCK-KCELL-1.0",
                "excitation_frequency_hz": 0.0,
                "excitation_amplitude_v": 1.0,
                "compensation_model": "linear_alpha",
            }
        ]
    )
    frame = store.get_frames(eid)[0]
    assert frame["calibration_id"] == "MOCK-KCELL-1.0"
    assert frame["device_id"] == "MOCK-IV-01"
    assert frame["compensation_model"] == "linear_alpha"
    recs = store.get_calibration_records(eid)
    assert recs[0]["calibration_id"] == "MOCK-KCELL-1.0"
    assert recs[0]["coeff_value"] == 1.0


def test_fit_results_replace_same_axis(store, tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DERIVED_DIR", str(tmp_path / "derived"))
    eid = store.create_experiment_with_sample("EXP-FIT", "fit", "S", "WIDE")
    model = {
        "model": "linear",
        "label": "线性",
        "params": {"a": 1},
        "r2": 0.9,
        "rmse": 0.1,
        "n": 5,
    }
    store.insert_fit_results(eid, "time", [{**model, "r2": 0.9}])
    store.insert_fit_results(eid, "time", [{**model, "r2": 0.99}])
    store.insert_fit_results(eid, "temperature", [{**model, "model": "arrhenius", "r2": 0.8}])
    rows = store.get_fit_results(eid)
    time_rows = [r for r in rows if r["x_axis"] == "time"]
    assert len(time_rows) == 1
    assert time_rows[0]["r2"] == 0.99
    assert len([r for r in rows if r["x_axis"] == "temperature"]) == 1
    path = store.write_fit_report(eid, {"x_axis": "time", "models": []})
    assert path.endswith("experiment_%s_fit_time.json" % eid)
    store.write_fit_report(eid, {"x_axis": "time", "models": [{"n": 1}]})
    assert len(list((tmp_path / "derived").glob("*.json"))) == 1
