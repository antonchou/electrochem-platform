"""SQLite 存储层测试：生命周期、append-only 约束、样品汇总、CSV 导出。"""

import os
import sqlite3
import time

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


def test_v5_migrates_legacy_not_null_columns_without_losing_raw_frames(tmp_path, monkeypatch):
    """旧 V1 库升级后允许严格 V2 的 legacy/温度空值，并保留审计约束。"""
    db_path = tmp_path / "legacy_v1.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                operator TEXT,
                objective TEXT,
                started_at_utc TEXT NOT NULL,
                ended_at_utc TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                sample_id TEXT,
                sensor_path_id TEXT,
                metadata_json TEXT
            );
            CREATE TABLE raw_frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment_id INTEGER NOT NULL REFERENCES experiments(id),
                sample_id TEXT,
                sensor_path_id TEXT NOT NULL,
                seq_no INTEGER,
                timestamp_utc TEXT,
                monotonic_ms INTEGER,
                t_seconds REAL,
                ec_raw REAL NOT NULL,
                temperature_raw REAL NOT NULL,
                k25 REAL,
                quality_flags TEXT,
                status TEXT,
                inserted_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE INDEX idx_frames_exp ON raw_frames(experiment_id, id);
            CREATE TRIGGER block_raw_frames_update
            BEFORE UPDATE ON raw_frames
            BEGIN
                SELECT RAISE(ABORT, 'raw_frames is append-only: UPDATE is not allowed');
            END;
            CREATE TRIGGER block_raw_frames_delete
            BEFORE DELETE ON raw_frames
            BEGIN
                SELECT RAISE(ABORT, 'raw_frames is append-only: DELETE is not allowed');
            END;
            INSERT INTO experiments
                (id, experiment_id, title, started_at_utc, status, sample_id, sensor_path_id)
            VALUES
                (7, 'EXP-LEGACY', 'legacy', '2026-08-19T00:00:00Z', 'stopped', 'S', 'OLD');
            INSERT INTO raw_frames
                (id, experiment_id, sample_id, sensor_path_id, seq_no, timestamp_utc,
                 monotonic_ms, t_seconds, ec_raw, temperature_raw, status, inserted_at_utc)
            VALUES
                (42, 7, 'S', 'OLD', 1, '2026-08-19T00:00:01Z',
                 1000, 0.1, 1413.0, 25.0, 'running', '2026-08-19T00:00:01.123Z');
            """
        )

    monkeypatch.setenv("EC_DB_PATH", str(db_path))
    from app import storage

    storage.init_db()

    with storage._managed_conn() as conn:
        info = {
            row["name"]: row
            for row in conn.execute("PRAGMA table_info(raw_frames)").fetchall()
        }
        assert info["legacy_ec_us_cm"]["notnull"] == 0
        assert info["temperature_raw_c"]["notnull"] == 0
        assert [
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ] == [1, 2, 3, 4, 5]
        migrated = conn.execute(
            "SELECT id, legacy_ec_us_cm, temperature_raw_c, inserted_at_utc "
            "FROM raw_frames"
        ).fetchone()
        assert tuple(migrated) == (
            42,
            1413.0,
            25.0,
            "2026-08-19T00:00:01.123Z",
        )
        objects = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('index', 'trigger') AND tbl_name = 'raw_frames'"
            ).fetchall()
        }
        assert {
            "idx_frames_exp",
            "block_raw_frames_update",
            "block_raw_frames_delete",
        }.issubset(objects)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name = 'raw_frames_v5_migration'"
        ).fetchone()[0] == 0

    storage.insert_frames(
        [
            {
                "experiment_id": 7,
                "sample_id": "S",
                "sensor_path_id": "OLD",
                "seq_no": 2,
                "timestamp_utc": "2026-08-19T00:00:02Z",
                "monotonic_ms": 2000,
                "t_seconds": 0.2,
                "schema_version": "2.0",
                "legacy_ec_us_cm": None,
                "temperature_raw_c": None,
                "quality_flags": "DROPOUT",
                "status": "running",
            }
        ]
    )
    frames = storage.get_frames(7)
    assert [frame["id"] for frame in frames] == [42, 43]
    assert frames[1]["legacy_ec_us_cm"] is None
    assert frames[1]["temperature_raw_c"] is None

    with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError)):
        with storage._managed_conn() as conn:
            conn.execute("UPDATE raw_frames SET status = 'changed' WHERE id = 42")
    with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError)):
        with storage._managed_conn() as conn:
            conn.execute("DELETE FROM raw_frames WHERE id = 42")

    # 重复初始化必须幂等，不重复迁移或改变历史数据。
    storage.init_db()
    assert storage.count_frames(7) == 2

    # 走完整应用链路，防止后台首批 V2 帧写入后再次把 writer 熔断为 HTTP 503。
    from fastapi.testclient import TestClient

    from app.main import app
    from app.persistence import persist

    with TestClient(app) as client:
        started = client.post("/api/experiment/start")
        assert started.status_code == 200
        experiment_id = started.json()["experiment_id"]
        time.sleep(0.7)  # 超过 0.5s 攒批窗口，确保真实执行 storage.insert_frames
        assert persist.failed is False
        stopped = client.post("/api/experiment/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "stopped"
        assert storage.count_frames(experiment_id) > 0


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
