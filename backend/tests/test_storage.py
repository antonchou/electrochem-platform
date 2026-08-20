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
        store._conn().execute("UPDATE raw_frames SET ec_raw = 999 WHERE experiment_id = ?", (eid,))
    with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError)):
        store._conn().execute("DELETE FROM raw_frames WHERE experiment_id = ?", (eid,))

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
    assert "sensor_path_id" in lines[0]
    assert "NACL_006" in lines[1]
