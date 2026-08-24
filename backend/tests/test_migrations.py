"""版本化迁移框架测试：V1 基线、真实 v2 迁移、旧库升级、事务回滚、幂等可复现。

纪律（docs/V2-regression-postmortem.md 教训 #2/#3/#6）：
- 旧库升级路径必须被测试覆盖（本文件 `_legacy_v1_db` 构造真实 V1 历史库）
- 迁移必须事务化：失败整体回滚，绝不出现半迁移状态
- 新库直建 与 旧库升级到同一版本后，表结构必须完全一致（幂等可复现）
- 框架机制用注入的演示迁移（SCHEMA_VERSION+1）验证；真实 v2–v5 单独断言
"""

import os
import sqlite3

import pytest

from app import storage


@pytest.fixture()
def fresh_db(tmp_path):
    """全新库（init_db 直接建 V1 结构并迁移到 SCHEMA_VERSION）。"""
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


def _inject_demo_migration(monkeypatch, fail: bool = False) -> int:
    """注入 SCHEMA_VERSION+1 的演示迁移，用于验证框架机制（回滚/幂等）。"""
    next_version = storage.SCHEMA_VERSION + 1

    def migrate_demo(conn):
        conn.execute("ALTER TABLE experiments ADD COLUMN operator_org TEXT")
        conn.execute("UPDATE experiments SET operator_org = 'lab'")
        if fail:
            raise RuntimeError("simulated migration failure")

    monkeypatch.setitem(storage.MIGRATIONS, next_version, migrate_demo)
    monkeypatch.setattr(storage, "SCHEMA_VERSION", next_version)
    return next_version


def _versions(conn):
    return [r["version"] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]


# ---------------- 基线 ----------------

def test_fresh_db_reaches_schema_version(fresh_db):
    with fresh_db._conn() as conn:
        assert _versions(conn) == list(range(1, storage.SCHEMA_VERSION + 1))
        # 四张核心表 + schema_migrations 都在
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "experiments",
        "samples",
        "raw_frames",
        "calibration_records",
        "schema_migrations",
        "fit_results",
    } <= tables


def test_idempotent_init_no_duplicate_versions(tmp_path):
    """init_db 多次调用不重复记录版本、不报错。"""
    os.environ["EC_DB_PATH"] = str(tmp_path / "idem.db")
    try:
        storage.init_db()
        storage.init_db()
        storage.init_db()
        with storage._conn() as conn:
            assert _versions(conn) == list(range(1, storage.SCHEMA_VERSION + 1))
    finally:
        os.environ.pop("EC_DB_PATH", None)


# ---------------- 真实 v2 迁移（I–V 计算链列） ----------------

def test_v2_adds_iv_columns(tmp_path):
    """真实 v2 迁移：raw_frames 增加 U/I/G/κ 列，数据与触发器保留。"""
    db = _legacy_v1_db(tmp_path)
    os.environ["EC_DB_PATH"] = str(db)
    try:
        storage.init_db()
        with storage._conn() as conn:
            assert _versions(conn) == list(range(1, storage.SCHEMA_VERSION + 1))
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(raw_frames)")}
            assert {
                "voltage_raw_v",
                "current_raw_a",
                "conductance_s",
                "kappa_t_us_cm",
                "kappa_25_us_cm",
            } <= cols
            # 旧数据仍在
            assert conn.execute("SELECT ec_raw FROM raw_frames").fetchone()["ec_raw"] == 1413.0
            trig = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
            }
    finally:
        os.environ.pop("EC_DB_PATH", None)
    assert {"block_raw_frames_update", "block_raw_frames_delete"} <= trig


# ---------------- 真实 v3 迁移（QC 列） ----------------

def test_v3_adds_qc_columns(tmp_path):
    """真实 v3 迁移：samples 增加判稳/QC 字段，数据与触发器保留。"""
    db = _legacy_v1_db(tmp_path)
    os.environ["EC_DB_PATH"] = str(db)
    try:
        storage.init_db()
        with storage._conn() as conn:
            assert _versions(conn) == list(range(1, storage.SCHEMA_VERSION + 1))
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(samples)")}
            assert {
                "qc_status",
                "qc_reason",
                "representative_value",
                "qc_checked_at_utc",
            } <= cols
            trig = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
            }
    finally:
        os.environ.pop("EC_DB_PATH", None)
    assert {"block_raw_frames_update", "block_raw_frames_delete"} <= trig


# ---------------- 真实 v4 迁移（ec_raw 解除 NOT NULL） ----------------

def test_v4_makes_ec_nullable(tmp_path):
    """真实 v4 迁移：raw_frames.ec_raw 允许 NULL（真实原始帧可无 ec），数据与触发器保留。"""
    db = _legacy_v1_db(tmp_path)
    os.environ["EC_DB_PATH"] = str(db)
    try:
        storage.init_db()
        with storage._conn() as conn:
            assert _versions(conn) == list(range(1, storage.SCHEMA_VERSION + 1))
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(raw_frames)")}
            assert "ec_raw" in cols
            # 旧数据仍在
            assert conn.execute("SELECT ec_raw FROM raw_frames").fetchone()["ec_raw"] == 1413.0
            trig = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
            }
    finally:
        os.environ.pop("EC_DB_PATH", None)
    assert {"block_raw_frames_update", "block_raw_frames_delete"} <= trig
    # 允许插入 NULL ec
    import sqlite3 as _s
    conn = _s.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("INSERT INTO raw_frames (experiment_id, sensor_path_id, ec_raw, temperature_raw, status) VALUES (1, 'CSV', NULL, 27.0, 'running')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM raw_frames WHERE ec_raw IS NULL").fetchone()[0] == 1
    conn.close()


# ---------------- 旧库升级 ----------------

def test_legacy_v1_db_upgrade_preserves_data(tmp_path):
    """真实 V1 旧库（带数据+触发器）升级后：数据保留、触发器保留、标记版本到当前。"""
    db = _legacy_v1_db(tmp_path)
    os.environ["EC_DB_PATH"] = str(db)
    try:
        storage.init_db()
        with storage._conn() as conn:
            assert _versions(conn) == list(range(1, storage.SCHEMA_VERSION + 1))
            assert conn.execute("SELECT COUNT(*) AS n FROM raw_frames").fetchone()["n"] == 1
            assert conn.execute("SELECT ec_raw FROM raw_frames").fetchone()["ec_raw"] == 1413.0
            trig = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
            }
    finally:
        os.environ.pop("EC_DB_PATH", None)
    assert {"block_raw_frames_update", "block_raw_frames_delete"} <= trig


# ---------------- 真实 v5 迁移（协议溯源 / 校准 / 激励列） ----------------

def test_v5_adds_trace_columns(tmp_path):
    """真实 v5 迁移：raw_frames 增加 calibration_id 与协议/激励字段，数据与触发器保留。"""
    db = _legacy_v1_db(tmp_path)
    os.environ["EC_DB_PATH"] = str(db)
    try:
        storage.init_db()
        with storage._conn() as conn:
            assert _versions(conn) == list(range(1, storage.SCHEMA_VERSION + 1))
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(raw_frames)")}
            assert {
                "schema_version",
                "device_id",
                "firmware_version",
                "range_id",
                "calibration_id",
                "excitation_frequency_hz",
                "excitation_amplitude_v",
                "compensation_model",
            } <= cols
            assert conn.execute("SELECT ec_raw FROM raw_frames").fetchone()["ec_raw"] == 1413.0
            trig = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
            }
    finally:
        os.environ.pop("EC_DB_PATH", None)
    assert {"block_raw_frames_update", "block_raw_frames_delete"} <= trig


def test_v6_creates_fit_results(tmp_path):
    """真实 v6 迁移：fit_results 表存在，旧数据与触发器保留。"""
    db = _legacy_v1_db(tmp_path)
    os.environ["EC_DB_PATH"] = str(db)
    try:
        storage.init_db()
        with storage._conn() as conn:
            assert _versions(conn) == list(range(1, storage.SCHEMA_VERSION + 1))
            tables = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert "fit_results" in tables
            trig = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
            }
    finally:
        os.environ.pop("EC_DB_PATH", None)
    assert {"block_raw_frames_update", "block_raw_frames_delete"} <= trig


# ---------------- 迁移机制：成功 / 回滚（注入 SCHEMA_VERSION+1） ----------------

def test_demo_migration_applies_transactionally(tmp_path, monkeypatch):
    """注入演示迁移：列新增、数据更新、版本记录。"""
    db = _legacy_v1_db(tmp_path)
    os.environ["EC_DB_PATH"] = str(db)
    try:
        storage.init_db()
        nxt = _inject_demo_migration(monkeypatch, fail=False)
        storage.init_db()
        with storage._conn() as conn:
            assert _versions(conn) == list(range(1, nxt + 1))
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(experiments)")}
            org = conn.execute("SELECT operator_org FROM experiments").fetchone()["operator_org"]
    finally:
        os.environ.pop("EC_DB_PATH", None)
    assert "operator_org" in cols
    assert org == "lab"


def test_migration_failure_rolls_back(tmp_path, monkeypatch):
    """迁移中途失败：整体回滚，版本不记录、DDL 撤销、原数据完好（无半迁移状态）。"""
    db = _legacy_v1_db(tmp_path)
    os.environ["EC_DB_PATH"] = str(db)
    try:
        storage.init_db()
        current = storage.SCHEMA_VERSION
        _inject_demo_migration(monkeypatch, fail=True)
        with pytest.raises(RuntimeError):
            storage.init_db()
        with storage._conn() as conn:
            assert _versions(conn) == list(range(1, current + 1))
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(experiments)")}
            n = conn.execute("SELECT COUNT(*) AS n FROM raw_frames").fetchone()["n"]
    finally:
        os.environ.pop("EC_DB_PATH", None)
    assert "operator_org" not in cols  # ALTER 已回滚
    assert n == 1  # 数据完好


# ---------------- 幂等可复现 ----------------

def test_fresh_and_upgraded_reach_same_schema(tmp_path, monkeypatch):
    """新库直建 vs 旧库升级到同一版本后，raw_frames 结构完全一致（V2 事故教训 #3）。"""
    fresh = tmp_path / "fresh_same.db"
    os.environ["EC_DB_PATH"] = str(fresh)
    storage.init_db()
    nxt = _inject_demo_migration(monkeypatch, fail=False)
    storage.init_db()
    with storage._conn() as conn:
        fresh_cols = [tuple(r) for r in conn.execute("PRAGMA table_info(raw_frames)")]
        fresh_versions = _versions(conn)
    os.environ.pop("EC_DB_PATH", None)

    legacy = _legacy_v1_db(tmp_path)
    os.environ["EC_DB_PATH"] = str(legacy)
    storage.init_db()
    storage.init_db()
    with storage._conn() as conn:
        legacy_cols = [tuple(r) for r in conn.execute("PRAGMA table_info(raw_frames)")]
        legacy_versions = _versions(conn)
    os.environ.pop("EC_DB_PATH", None)

    assert fresh_versions == legacy_versions == list(range(1, nxt + 1))
    assert fresh_cols == legacy_cols
