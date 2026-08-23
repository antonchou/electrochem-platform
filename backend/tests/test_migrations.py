"""版本化迁移框架测试：V1 基线、旧库升级、事务回滚、幂等可复现。

纪律（docs/V2-regression-postmortem.md 教训 #2/#3/#6）：
- 旧库升级路径必须被测试覆盖（本文件 `_legacy_v1_db` 构造真实 V1 历史库）
- 迁移必须事务化：失败整体回滚，绝不出现半迁移状态
- 新库直建 与 旧库升级到同一版本后，表结构必须完全一致（幂等可复现）
"""

import os
import sqlite3

import pytest

from app import storage


@pytest.fixture()
def fresh_db(tmp_path):
    """全新库（init_db 直接建 V1 结构并标记 version 1）。"""
    os.environ["EC_DB_PATH"] = str(tmp_path / "migration_fresh.db")
    storage.init_db()
    yield storage
    os.environ.pop("EC_DB_PATH", None)


def _legacy_v1_db(tmp_path):
    """构造一个不带 schema_migrations 的历史 V1 库（含数据 + 触发器），模拟真实旧库。"""
    db = tmp_path / "legacy_v1.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE experiments (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id  TEXT UNIQUE NOT NULL,
            title          TEXT NOT NULL,
            operator       TEXT,
            objective      TEXT,
            started_at_utc TEXT NOT NULL,
            ended_at_utc   TEXT,
            status         TEXT NOT NULL DEFAULT 'running',
            sample_id      TEXT,
            sensor_path_id TEXT,
            metadata_json  TEXT
        );
        CREATE TABLE samples (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id          INTEGER NOT NULL REFERENCES experiments(id),
            sample_id              TEXT NOT NULL,
            sensor_path_id         TEXT,
            concentration_mmol_l   REAL,
            composition            TEXT,
            preparation_record_id  TEXT,
            measured_at_utc        TEXT,
            k25_median             REAL,
            k25_mean               REAL,
            k25_sd                 REAL,
            frame_count            INTEGER NOT NULL DEFAULT 0,
            UNIQUE(experiment_id, sample_id, sensor_path_id)
        );
        CREATE TABLE raw_frames (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id  INTEGER NOT NULL REFERENCES experiments(id),
            sample_id      TEXT,
            sensor_path_id TEXT NOT NULL,
            seq_no         INTEGER,
            timestamp_utc  TEXT,
            monotonic_ms   INTEGER,
            t_seconds      REAL,
            ec_raw         REAL NOT NULL,
            temperature_raw REAL NOT NULL,
            k25            REAL,
            quality_flags  TEXT,
            status         TEXT,
            inserted_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE INDEX idx_frames_exp ON raw_frames(experiment_id, id);
        CREATE TABLE calibration_records (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER REFERENCES experiments(id),
            calibration_id TEXT NOT NULL,
            sensor_path_id TEXT,
            mode          TEXT,
            standard      TEXT,
            lot           TEXT,
            coeff_value   REAL,
            coeff_json    TEXT,
            created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        CREATE TRIGGER block_raw_frames_update
        BEFORE UPDATE ON raw_frames
        BEGIN SELECT RAISE(ABORT, 'raw_frames is append-only: UPDATE is not allowed'); END;
        CREATE TRIGGER block_raw_frames_delete
        BEFORE DELETE ON raw_frames
        BEGIN SELECT RAISE(ABORT, 'raw_frames is append-only: DELETE is not allowed'); END;
        INSERT INTO experiments (experiment_id, title, started_at_utc, status)
            VALUES ('LEGACY-001', 'legacy', '2026-08-19T00:00:00Z', 'stopped');
        INSERT INTO raw_frames (experiment_id, sensor_path_id, ec_raw, temperature_raw, status)
            VALUES (1, 'CM2_WIDE', 1413.0, 25.0, 'running');
        """
    )
    conn.commit()
    conn.close()
    return db


def _inject_v2(monkeypatch, fail: bool = False):
    """模拟“发布 v2 迁移”：注册迁移并同步提升 SCHEMA_VERSION（版本门禁要求）。

    注意：演示迁移不 UPDATE raw_frames——它有 append-only 触发器（REQ-D-001），
    任何会改 raw_frames 数据的迁移都必须先处理该触发器（V2 的 388b065 重建表时
    触发器会随表一起被 DROP，须重建）。这里数据变更放到 experiments 表验证。
    """

    def migrate_v2(conn):
        conn.execute("ALTER TABLE raw_frames ADD COLUMN voltage_raw_v REAL")
        conn.execute("ALTER TABLE experiments ADD COLUMN operator_org TEXT")
        conn.execute("UPDATE experiments SET operator_org = 'lab'")
        if fail:
            raise RuntimeError("simulated migration failure")

    monkeypatch.setitem(storage.MIGRATIONS, 2, migrate_v2)
    monkeypatch.setattr(storage, "SCHEMA_VERSION", 2)


def _versions(conn):
    return [r["version"] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


# ---------------- 基线 ----------------

def test_fresh_db_marks_version_1(fresh_db):
    with fresh_db._conn() as conn:
        assert _versions(conn) == [1]
        # 四张核心表 + schema_migrations 都在
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"experiments", "samples", "raw_frames", "calibration_records", "schema_migrations"} <= tables


def test_idempotent_init_no_duplicate_versions(tmp_path):
    """init_db 多次调用不重复记录版本、不报错。"""
    os.environ["EC_DB_PATH"] = str(tmp_path / "idem.db")
    try:
        storage.init_db()
        storage.init_db()
        storage.init_db()
        with storage._conn() as conn:
            assert _versions(conn) == [1]
    finally:
        os.environ.pop("EC_DB_PATH", None)


# ---------------- 旧库升级 ----------------

def test_legacy_v1_db_upgrade_preserves_data(tmp_path):
    """真实 V1 旧库（带数据+触发器）升级后：数据保留、触发器保留、标记 version 1。"""
    db = _legacy_v1_db(tmp_path)
    os.environ["EC_DB_PATH"] = str(db)
    try:
        storage.init_db()
        with storage._conn() as conn:
            assert _versions(conn) == [1]
            assert conn.execute("SELECT COUNT(*) AS n FROM raw_frames").fetchone()["n"] == 1
            assert conn.execute("SELECT ec_raw FROM raw_frames").fetchone()["ec_raw"] == 1413.0
            trig = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
            }
    finally:
        os.environ.pop("EC_DB_PATH", None)
    assert {"block_raw_frames_update", "block_raw_frames_delete"} <= trig


# ---------------- 迁移机制：成功 / 回滚 ----------------

def test_migration_v2_applies_transactionally(tmp_path, monkeypatch):
    """注入 v2 迁移：从 v1 旧库升级到 v2，列新增、数据更新、版本记录 [1,2]。"""
    db = _legacy_v1_db(tmp_path)
    os.environ["EC_DB_PATH"] = str(db)
    try:
        storage.init_db()  # 到 v1
        _inject_v2(monkeypatch, fail=False)
        storage.init_db()  # 升级到 v2
        with storage._conn() as conn:
            assert _versions(conn) == [1, 2]
            rf_cols = [r["name"] for r in conn.execute("PRAGMA table_info(raw_frames)")]
            exp_cols = [r["name"] for r in conn.execute("PRAGMA table_info(experiments)")]
            org = conn.execute("SELECT operator_org FROM experiments").fetchone()["operator_org"]
    finally:
        os.environ.pop("EC_DB_PATH", None)
    assert "voltage_raw_v" in rf_cols
    assert "operator_org" in exp_cols
    assert org == "lab"


def test_migration_failure_rolls_back(tmp_path, monkeypatch):
    """迁移中途失败：整体回滚，版本不记录、DDL 撤销、原数据完好（无半迁移状态）。"""
    db = _legacy_v1_db(tmp_path)
    os.environ["EC_DB_PATH"] = str(db)
    try:
        storage.init_db()
        _inject_v2(monkeypatch, fail=True)
        with pytest.raises(RuntimeError):
            storage.init_db()
        with storage._conn() as conn:
            assert _versions(conn) == [1]  # v2 未记录
            rf_cols = [r["name"] for r in conn.execute("PRAGMA table_info(raw_frames)")]
            exp_cols = [r["name"] for r in conn.execute("PRAGMA table_info(experiments)")]
            n = conn.execute("SELECT COUNT(*) AS n FROM raw_frames").fetchone()["n"]
    finally:
        os.environ.pop("EC_DB_PATH", None)
    assert "voltage_raw_v" not in rf_cols  # ALTER 已回滚
    assert "operator_org" not in exp_cols  # ALTER 已回滚
    assert n == 1  # 数据完好


# ---------------- 幂等可复现 ----------------

def test_fresh_and_upgraded_reach_same_schema(tmp_path, monkeypatch):
    """新库直建 vs 旧库升级到同一版本后，raw_frames 结构完全一致（V2 事故教训 #3）。"""
    # 新库直建（含 v2 迁移后）
    fresh = tmp_path / "fresh_same.db"
    os.environ["EC_DB_PATH"] = str(fresh)
    storage.init_db()
    _inject_v2(monkeypatch, fail=False)
    storage.init_db()
    with storage._conn() as conn:
        fresh_cols = [tuple(r) for r in conn.execute("PRAGMA table_info(raw_frames)")]
    os.environ.pop("EC_DB_PATH", None)

    # 旧库升级
    legacy = _legacy_v1_db(tmp_path)
    os.environ["EC_DB_PATH"] = str(legacy)
    storage.init_db()
    _inject_v2(monkeypatch, fail=False)
    storage.init_db()
    with storage._conn() as conn:
        legacy_cols = [tuple(r) for r in conn.execute("PRAGMA table_info(raw_frames)")]
    os.environ.pop("EC_DB_PATH", None)

    assert fresh_cols == legacy_cols
