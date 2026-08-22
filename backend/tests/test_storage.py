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
    eid = store.create_experiment(
        "EXP-TEST-001",
        "不同溶液导电性相对比较",
        sample_id="NACL_004",
        sensor_path_id="CM2_WIDE",
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


def test_raw_frames_append_only(store):
    """REQ-D-001：原始帧只追加，UPDATE/DELETE 均被拒绝。"""
    eid = store.create_experiment("EXP-002", "t", sample_id="S", sensor_path_id="CM2_WIDE")
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
        with store._managed_conn() as conn:
            conn.execute(
                "UPDATE raw_frames SET legacy_ec_us_cm = 999 WHERE experiment_id = ?",
                (eid,),
            )
    with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError)):
        with store._managed_conn() as conn:
            conn.execute("DELETE FROM raw_frames WHERE experiment_id = ?", (eid,))

    # 数据仍然完好
    assert store.count_frames(eid) == 1


def test_sample_upsert_accumulates(store):
    eid = store.create_experiment("EXP-003", "t", sample_id="NACL_002", sensor_path_id="CM2_WIDE")
    store.upsert_sample(eid, "NACL_002", "CM2_WIDE", concentration_mmol_l=2.0, frame_count_delta=5)
    store.upsert_sample(eid, "NACL_002", "CM2_WIDE", frame_count_delta=7)
    samples = store.get_samples(eid)
    assert len(samples) == 1
    assert samples[0]["frame_count"] == 12
    assert samples[0]["concentration_mmol_l"] == 2.0


def test_sample_upsert_refreshes_trace_fields(store):
    eid = store.create_experiment("EXP-003B", "t", sample_id="S", sensor_path_id="WIDE")
    store.upsert_sample(
        eid,
        "S",
        "WIDE",
        concentration_mmol_l=1.0,
        composition="old",
        preparation_record_id="PREP-1",
    )
    first = store.get_samples(eid)[0]
    store.upsert_sample(
        eid,
        "S",
        "WIDE",
        concentration_mmol_l=2.0,
        composition="new",
        preparation_record_id="PREP-2",
    )
    current = store.get_samples(eid)[0]
    assert current["concentration_mmol_l"] == 2.0
    assert current["composition"] == "new"
    assert current["preparation_record_id"] == "PREP-2"
    assert current["measured_at_utc"] >= first["measured_at_utc"]


def test_insert_frames_falls_back_when_new_legacy_keys_are_none(store):
    eid = store.create_experiment("EXP-FALLBACK", "t", sample_id="S", sensor_path_id="WIDE")
    store.insert_frames(
        [
            {
                "experiment_id": eid,
                "sample_id": "S",
                "sensor_path_id": "WIDE",
                "legacy_ec_us_cm": None,
                "ec_raw": 123.0,
                "temperature_raw_c": None,
                "temperature_raw": 24.5,
            }
        ]
    )
    frame = store.get_frames(eid)[0]
    assert frame["legacy_ec_us_cm"] == 123.0
    assert frame["temperature_raw_c"] == 24.5


def test_history_frames_expose_structured_quality_flags(store):
    eid = store.create_experiment("EXP-FLAGS", "t", sample_id="S", sensor_path_id="WIDE")
    store.insert_frames(
        [
            {
                "experiment_id": eid,
                "sample_id": "S",
                "sensor_path_id": "WIDE",
                "quality_flags": "SIMULATED|DROPOUT",
            }
        ]
    )

    frame = store.get_frames(eid)[0]
    assert frame["quality_flags"] == "SIMULATED|DROPOUT"
    assert frame["quality_flags_list"] == ["SIMULATED", "DROPOUT"]


def test_export_csv(store):
    eid = store.create_experiment("EXP-004", "t", sample_id="NACL_006", sensor_path_id="CM2_WIDE")
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
    header = lines[0].split(",")
    assert "sensor_path_id" in header
    assert "kappa_25_us_cm" in header
    assert "k25" not in header
    assert "NACL_006" in lines[1]
