"""SQLite 存储层（Phase 7 数据存储与导出）。

- experiments / samples / raw_frames / calibration_records 四张核心表
- raw_frames 通过触发器强制 append-only：不允许 UPDATE / DELETE，保证原始数据不可篡改
- 所有 DAO 均同步执行，由调用方通过 asyncio.to_thread 放到线程池，避免阻塞事件循环
- DB 路径可用环境变量 EC_DB_PATH 覆盖（测试用临时库）
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

# 仓库约定：原始数据不可变，统一存 data/raw/（backend/app/storage.py → 仓库根/data/raw/ec.db）
DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / "ec.db"
FINAL_EXPERIMENT_STATUSES = frozenset({"stopped", "aborted", "error"})


def _db_path() -> str:
    return os.environ.get("EC_DB_PATH", str(DEFAULT_DB))


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS experiments (
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

CREATE TABLE IF NOT EXISTS samples (
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

CREATE TABLE IF NOT EXISTS raw_frames (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id  INTEGER NOT NULL REFERENCES experiments(id),
    sample_id      TEXT,
    sensor_path_id TEXT NOT NULL,
    seq_no         INTEGER,
    timestamp_utc  TEXT,
    monotonic_ms   INTEGER,
    t_seconds      REAL,
    schema_version TEXT,
    -- Raw（不可变原始量）
    voltage_raw_v  REAL,
    current_raw_a  REAL,
    temperature_raw_c REAL,
    -- Calibrated
    voltage_cal_v  REAL,
    current_cal_a  REAL,
    conductance_s  REAL,
    kappa_t_us_cm  REAL,
    -- Derived
    kappa_25_us_cm REAL,
    k25            REAL,
    -- Configuration（随实验版本化）
    excitation_frequency_hz REAL,
    excitation_amplitude_v  REAL,
    range_id       TEXT,
    compensation_model TEXT,
    alpha_per_c    REAL,
    -- Trace
    calibration_id TEXT,
    cell_constant_cm_inv REAL,
    calibration_valid_until_utc TEXT,
    -- 兼容（历史遗留；V1 ec 废弃别名迁移至此，不再是主字段）
    legacy_ec_us_cm REAL,
    quality_flags  TEXT,
    status         TEXT,
    inserted_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_frames_exp ON raw_frames(experiment_id, id);

CREATE TABLE IF NOT EXISTS calibration_records (
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

-- 原始数据只追加、不可篡改（REQ-D-001）
CREATE TRIGGER IF NOT EXISTS block_raw_frames_update
BEFORE UPDATE ON raw_frames
BEGIN
    SELECT RAISE(ABORT, 'raw_frames is append-only: UPDATE is not allowed');
END;

CREATE TRIGGER IF NOT EXISTS block_raw_frames_delete
BEFORE DELETE ON raw_frames
BEGIN
    SELECT RAISE(ABORT, 'raw_frames is append-only: DELETE is not allowed');
END;
"""


# ==========================================================================
# 版本化 SQLite 迁移（不手工改树莓派数据库；保留已有历史实验）
#
# 每个版本是独立函数，内部先 PRAGMA 检查列存在性再执行，保证幂等；
# schema_migrations 表记录已应用版本，应用启动时自动推进到最新。
# ==========================================================================

SCHEMA_VERSION = 5


RAW_FRAME_COLUMNS = (
    "id",
    "experiment_id",
    "sample_id",
    "sensor_path_id",
    "seq_no",
    "timestamp_utc",
    "monotonic_ms",
    "t_seconds",
    "schema_version",
    "voltage_raw_v",
    "current_raw_a",
    "temperature_raw_c",
    "voltage_cal_v",
    "current_cal_a",
    "conductance_s",
    "kappa_t_us_cm",
    "kappa_25_us_cm",
    "k25",
    "excitation_frequency_hz",
    "excitation_amplitude_v",
    "range_id",
    "compensation_model",
    "alpha_per_c",
    "calibration_id",
    "cell_constant_cm_inv",
    "calibration_valid_until_utc",
    "legacy_ec_us_cm",
    "quality_flags",
    "status",
    "inserted_at_utc",
)


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(
    conn: sqlite3.Connection,
    name: str,
    declaration: str,
    existing: set[str],
) -> None:
    if name not in existing:
        conn.execute(f"ALTER TABLE raw_frames ADD COLUMN {name} {declaration}")


def _rename_column_if_present(
    conn: sqlite3.Connection,
    old: str,
    new: str,
    existing: set[str],
) -> None:
    # 老列存在且新列不存在时才重命名（新库直接建新列）
    if old in existing and new not in existing:
        conn.execute(f"ALTER TABLE raw_frames RENAME COLUMN {old} TO {new}")


def _migrate_v1_iv_columns(conn: sqlite3.Connection) -> None:
    """v1：补齐电极 I–V 链路基础列（Raw/Calibrated/Derived/Trace）。"""
    existing = _column_names(conn, "raw_frames")
    for name, decl in {
        "voltage_raw_v": "REAL",
        "current_raw_a": "REAL",
        "conductance_s": "REAL",
        "kappa_t_us_cm": "REAL",
        "kappa_25_us_cm": "REAL",
        "calibration_id": "TEXT",
    }.items():
        _add_column_if_missing(conn, name, decl, existing)


def _migrate_v2_rename_legacy(conn: sqlite3.Connection) -> None:
    """v2：列名对齐 V2 协议——temperature_raw→temperature_raw_c；ec_raw→legacy_ec_us_cm。"""
    existing = _column_names(conn, "raw_frames")
    _rename_column_if_present(conn, "temperature_raw", "temperature_raw_c", existing)
    _rename_column_if_present(conn, "ec_raw", "legacy_ec_us_cm", existing)


def _migrate_v3_extra_columns(conn: sqlite3.Connection) -> None:
    """v3：补齐配置层与校准列（schema_version/通道校准/激励/量程/温补模型）。"""
    existing = _column_names(conn, "raw_frames")
    for name, decl in {
        "schema_version": "TEXT",
        "voltage_cal_v": "REAL",
        "current_cal_a": "REAL",
        "excitation_frequency_hz": "REAL",
        "excitation_amplitude_v": "REAL",
        "range_id": "TEXT",
        "compensation_model": "TEXT",
        "alpha_per_c": "REAL",
    }.items():
        _add_column_if_missing(conn, name, decl, existing)


def _migrate_v4_calibration_trace(conn: sqlite3.Connection) -> None:
    """v4：每帧保存 Kcell 与校准有效期，使 κ(T)/κ25 可独立追溯回算。"""
    existing = _column_names(conn, "raw_frames")
    for name, decl in {
        "cell_constant_cm_inv": "REAL",
        "calibration_valid_until_utc": "TEXT",
    }.items():
        _add_column_if_missing(conn, name, decl, existing)


def _migrate_v5_nullable_legacy_columns(conn: sqlite3.Connection) -> None:
    """v5：解除旧 V1 必填列经重命名后遗留的 NOT NULL 约束。

    旧库的 ``ec_raw`` / ``temperature_raw`` 均为 NOT NULL；v2 只重命名列，
    SQLite 会保留原约束。严格 V2 帧不再伪造 legacy EC，质量帧也允许温度为空，
    因此必须重建表（SQLite 不支持直接 DROP NOT NULL）。整个迁移在调用方事务中
    完成，并保留原始行 id、插入时间、索引及 append-only 触发器。
    """
    info = {
        row["name"]: row
        for row in conn.execute("PRAGMA table_info(raw_frames)").fetchall()
    }
    required = set(RAW_FRAME_COLUMNS)
    missing = sorted(required.difference(info))
    if missing:
        raise RuntimeError(
            "raw_frames schema is incomplete before v5 migration: " + ", ".join(missing)
        )

    nullable_targets = ("legacy_ec_us_cm", "temperature_raw_c")
    if all(int(info[name]["notnull"]) == 0 for name in nullable_targets):
        return

    old_count = int(conn.execute("SELECT COUNT(*) FROM raw_frames").fetchone()[0])
    migration_table = "raw_frames_v5_migration"
    conn.execute(f"DROP TABLE IF EXISTS {migration_table}")
    conn.execute(
        f"""
        CREATE TABLE {migration_table} (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id  INTEGER NOT NULL REFERENCES experiments(id),
            sample_id      TEXT,
            sensor_path_id TEXT NOT NULL,
            seq_no         INTEGER,
            timestamp_utc  TEXT,
            monotonic_ms   INTEGER,
            t_seconds      REAL,
            schema_version TEXT,
            voltage_raw_v  REAL,
            current_raw_a  REAL,
            temperature_raw_c REAL,
            voltage_cal_v  REAL,
            current_cal_a  REAL,
            conductance_s  REAL,
            kappa_t_us_cm  REAL,
            kappa_25_us_cm REAL,
            k25            REAL,
            excitation_frequency_hz REAL,
            excitation_amplitude_v  REAL,
            range_id       TEXT,
            compensation_model TEXT,
            alpha_per_c    REAL,
            calibration_id TEXT,
            cell_constant_cm_inv REAL,
            calibration_valid_until_utc TEXT,
            legacy_ec_us_cm REAL,
            quality_flags  TEXT,
            status         TEXT,
            inserted_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
        """
    )
    columns_sql = ", ".join(f'"{name}"' for name in RAW_FRAME_COLUMNS)
    conn.execute(
        f"INSERT INTO {migration_table} ({columns_sql}) "
        f"SELECT {columns_sql} FROM raw_frames"
    )
    copied_count = int(
        conn.execute(f"SELECT COUNT(*) FROM {migration_table}").fetchone()[0]
    )
    if copied_count != old_count:
        raise RuntimeError(
            f"raw_frames v5 migration row-count mismatch: {old_count} != {copied_count}"
        )

    # 旧表的保护触发器和索引随表替换后需要显式重建。
    conn.execute("DROP TRIGGER IF EXISTS block_raw_frames_update")
    conn.execute("DROP TRIGGER IF EXISTS block_raw_frames_delete")
    conn.execute("DROP TABLE raw_frames")
    conn.execute(f"ALTER TABLE {migration_table} RENAME TO raw_frames")
    conn.execute("CREATE INDEX idx_frames_exp ON raw_frames(experiment_id, id)")
    conn.execute(
        """
        CREATE TRIGGER block_raw_frames_update
        BEFORE UPDATE ON raw_frames
        BEGIN
            SELECT RAISE(ABORT, 'raw_frames is append-only: UPDATE is not allowed');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER block_raw_frames_delete
        BEFORE DELETE ON raw_frames
        BEGIN
            SELECT RAISE(ABORT, 'raw_frames is append-only: DELETE is not allowed');
        END
        """
    )

    migrated_info = {
        row["name"]: row
        for row in conn.execute("PRAGMA table_info(raw_frames)").fetchall()
    }
    if any(int(migrated_info[name]["notnull"]) != 0 for name in nullable_targets):
        raise RuntimeError("raw_frames v5 migration did not relax legacy nullability")
    final_count = int(conn.execute("SELECT COUNT(*) FROM raw_frames").fetchone()[0])
    if final_count != old_count:
        raise RuntimeError(
            f"raw_frames v5 final row-count mismatch: {old_count} != {final_count}"
        )


MIGRATIONS: Dict[int, Any] = {
    1: _migrate_v1_iv_columns,
    2: _migrate_v2_rename_legacy,
    3: _migrate_v3_extra_columns,
    4: _migrate_v4_calibration_trace,
    5: _migrate_v5_nullable_legacy_columns,
}


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """按版本顺序应用迁移（幂等）；新库已含全部列，仅记录版本。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
        """
    )
    applied = {
        row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }
    for version, apply in sorted(MIGRATIONS.items()):
        if version in applied:
            continue
        apply(conn)
        conn.execute(
            "INSERT INTO schema_migrations (version) VALUES (?)", (version,)
        )


def _conn() -> sqlite3.Connection:
    db = _db_path()
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _managed_conn() -> Iterator[sqlite3.Connection]:
    """保留 sqlite3 事务提交/回滚语义，并在退出时确定性关闭连接。"""
    conn = _conn()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    """建表（幂等）+ 版本化迁移。应用启动时调用；旧库自动升级，不手工改库。"""
    with _managed_conn() as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)


# ---------------- 实验生命周期 ----------------

def create_experiment(
    experiment_id: str,
    title: str,
    *,
    operator: Optional[str] = None,
    objective: Optional[str] = None,
    sample_id: Optional[str] = None,
    sensor_path_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    started_at_utc: Optional[str] = None,
) -> int:
    import datetime

    started = started_at_utc or datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    with _managed_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO experiments
                (experiment_id, title, operator, objective, started_at_utc, status,
                 sample_id, sensor_path_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)
            """,
            (
                experiment_id,
                title,
                operator,
                objective,
                started,
                sample_id,
                sensor_path_id,
                json.dumps(metadata, ensure_ascii=False) if metadata else None,
            ),
        )
        return int(cur.lastrowid)


def create_experiment_with_sample(
    experiment_id: str,
    title: str,
    sample_id: str,
    sensor_path_id: str,
    *,
    operator: Optional[str] = None,
    objective: Optional[str] = None,
    concentration_mmol_l: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
    started_at_utc: Optional[str] = None,
) -> int:
    """原子创建实验和首个样品；任一步失败都不留下半成品记录。"""
    import datetime

    started = started_at_utc or datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    measured = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with _managed_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO experiments
                (experiment_id, title, operator, objective, started_at_utc, status,
                 sample_id, sensor_path_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)
            """,
            (
                experiment_id,
                title,
                operator,
                objective,
                started,
                sample_id,
                sensor_path_id,
                json.dumps(metadata, ensure_ascii=False) if metadata else None,
            ),
        )
        exp_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO samples
                (experiment_id, sample_id, sensor_path_id, concentration_mmol_l,
                 measured_at_utc, frame_count)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (exp_id, sample_id, sensor_path_id, concentration_mmol_l, measured),
        )
        return exp_id


def finish_experiment(experiment_id: int, status: str = "stopped") -> None:
    import datetime

    if status not in FINAL_EXPERIMENT_STATUSES:
        allowed = ", ".join(sorted(FINAL_EXPERIMENT_STATUSES))
        raise ValueError(f"invalid terminal experiment status {status!r}; expected one of: {allowed}")
    ended = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with _managed_conn() as conn:
        conn.execute(
            "UPDATE experiments SET status = ?, ended_at_utc = ? WHERE id = ?",
            (status, ended, experiment_id),
        )


# ---------------- 样品 ----------------

def upsert_sample(
    experiment_id: int,
    sample_id: str,
    sensor_path_id: str,
    *,
    concentration_mmol_l: Optional[float] = None,
    composition: Optional[str] = None,
    preparation_record_id: Optional[str] = None,
    frame_count_delta: int = 0,
    k25_median: Optional[float] = None,
    k25_mean: Optional[float] = None,
    k25_sd: Optional[float] = None,
) -> None:
    import datetime

    with _managed_conn() as conn:
        conn.execute(
            """
            INSERT INTO samples
                (experiment_id, sample_id, sensor_path_id, concentration_mmol_l,
                 composition, preparation_record_id, measured_at_utc,
                 k25_median, k25_mean, k25_sd, frame_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(experiment_id, sample_id, sensor_path_id) DO UPDATE SET
                frame_count = frame_count + excluded.frame_count,
                concentration_mmol_l = COALESCE(excluded.concentration_mmol_l, samples.concentration_mmol_l),
                composition = COALESCE(excluded.composition, samples.composition),
                preparation_record_id = COALESCE(excluded.preparation_record_id, samples.preparation_record_id),
                measured_at_utc = excluded.measured_at_utc,
                k25_median  = COALESCE(excluded.k25_median, samples.k25_median),
                k25_mean    = COALESCE(excluded.k25_mean, samples.k25_mean),
                k25_sd      = COALESCE(excluded.k25_sd, samples.k25_sd)
            """,
            (
                experiment_id,
                sample_id,
                sensor_path_id,
                concentration_mmol_l,
                composition,
                preparation_record_id,
                datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                k25_median,
                k25_mean,
                k25_sd,
                frame_count_delta,
            ),
        )


# ---------------- 原始帧 ----------------

def insert_frames(frames: List[Dict[str, Any]]) -> None:
    """批量写入原始帧（append-only），并累加对应样品的 frame_count（P2-4）。

    兼容旧调用方：缺省 I–V 列用 None 填充（老记录 U/I 允许 NULL）。
    """
    if not frames:
        return
    with _managed_conn() as conn:
        rows = []
        for f in frames:
            rows.append(
                {
                    "experiment_id": f["experiment_id"],
                    "sample_id": f.get("sample_id"),
                    "sensor_path_id": f["sensor_path_id"],
                    "seq_no": f.get("seq_no"),
                    "timestamp_utc": f.get("timestamp_utc"),
                    "monotonic_ms": f.get("monotonic_ms"),
                    "t_seconds": f.get("t_seconds"),
                    "schema_version": f.get("schema_version"),
                    # legacy = V1 ec 废弃别名迁移列；兼容旧调用方字段 ec_raw
                    "legacy_ec_us_cm": (
                        f.get("legacy_ec_us_cm")
                        if f.get("legacy_ec_us_cm") is not None
                        else f.get("ec_raw")
                    ),
                    # temperature_raw_c 优先，兼容旧调用方字段 temperature_raw
                    "temperature_raw_c": (
                        f.get("temperature_raw_c")
                        if f.get("temperature_raw_c") is not None
                        else f.get("temperature_raw")
                    ),
                    "voltage_raw_v": f.get("voltage_raw_v"),
                    "current_raw_a": f.get("current_raw_a"),
                    "voltage_cal_v": f.get("voltage_cal_v"),
                    "current_cal_a": f.get("current_cal_a"),
                    "conductance_s": f.get("conductance_s"),
                    "kappa_t_us_cm": f.get("kappa_t_us_cm"),
                    "kappa_25_us_cm": f.get("kappa_25_us_cm"),
                    "k25": f.get("k25"),
                    "excitation_frequency_hz": f.get("excitation_frequency_hz"),
                    "excitation_amplitude_v": f.get("excitation_amplitude_v"),
                    "range_id": f.get("range_id"),
                    "compensation_model": f.get("compensation_model"),
                    "alpha_per_c": f.get("alpha_per_c"),
                    "calibration_id": f.get("calibration_id"),
                    "cell_constant_cm_inv": f.get("cell_constant_cm_inv"),
                    "calibration_valid_until_utc": f.get("calibration_valid_until_utc"),
                    "quality_flags": f.get("quality_flags"),
                    "status": f.get("status"),
                }
            )
        conn.executemany(
            """
            INSERT INTO raw_frames
                (experiment_id, sample_id, sensor_path_id, seq_no, timestamp_utc,
                 monotonic_ms, t_seconds, schema_version,
                 legacy_ec_us_cm, temperature_raw_c,
                 voltage_raw_v, current_raw_a, voltage_cal_v, current_cal_a,
                 conductance_s, kappa_t_us_cm, kappa_25_us_cm, k25,
                 excitation_frequency_hz, excitation_amplitude_v, range_id,
                 compensation_model, alpha_per_c, calibration_id,
                 cell_constant_cm_inv, calibration_valid_until_utc,
                 quality_flags, status)
            VALUES (:experiment_id, :sample_id, :sensor_path_id, :seq_no, :timestamp_utc,
                    :monotonic_ms, :t_seconds, :schema_version,
                    :legacy_ec_us_cm, :temperature_raw_c,
                    :voltage_raw_v, :current_raw_a, :voltage_cal_v, :current_cal_a,
                    :conductance_s, :kappa_t_us_cm, :kappa_25_us_cm, :k25,
                    :excitation_frequency_hz, :excitation_amplitude_v, :range_id,
                    :compensation_model, :alpha_per_c, :calibration_id,
                    :cell_constant_cm_inv, :calibration_valid_until_utc,
                    :quality_flags, :status)
            """,
            rows,
        )
        # 必须按完整样品链路聚合；同一样品编号可能同时走 WIDE/NARROW 等不同通道。
        counts: Dict[tuple[int, str, str], int] = {}
        for f in frames:
            sample_id = f.get("sample_id")
            if sample_id is None:
                continue
            key = (f["experiment_id"], sample_id, f["sensor_path_id"])
            counts[key] = counts.get(key, 0) + 1
        for (exp_id, sample_id, sensor_path_id), n in counts.items():
            conn.execute(
                """UPDATE samples
                   SET frame_count = frame_count + ?
                   WHERE experiment_id = ? AND sample_id = ? AND sensor_path_id = ?""",
                (n, exp_id, sample_id, sensor_path_id),
            )


# ---------------- 查询 ----------------

def list_experiments() -> List[Dict[str, Any]]:
    with _managed_conn() as conn:
        rows = conn.execute(
            """
            SELECT e.*, COUNT(f.id) AS frame_count
            FROM experiments e
            LEFT JOIN raw_frames f ON f.experiment_id = e.id
            GROUP BY e.id
            ORDER BY e.id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_experiment(experiment_id: int) -> Optional[Dict[str, Any]]:
    with _managed_conn() as conn:
        row = conn.execute(
            "SELECT * FROM experiments WHERE id = ?", (experiment_id,)
        ).fetchone()
        return dict(row) if row else None


def get_samples(experiment_id: int) -> List[Dict[str, Any]]:
    with _managed_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM samples WHERE experiment_id = ? ORDER BY id", (experiment_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_frames(
    experiment_id: int,
    *,
    limit: int = 1000,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    if not 1 <= limit <= 1_000_000:
        raise ValueError("limit must be between 1 and 1000000")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    with _managed_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, sample_id, sensor_path_id, seq_no, timestamp_utc,
                   monotonic_ms, t_seconds, schema_version,
                   legacy_ec_us_cm, temperature_raw_c,
                   voltage_raw_v, current_raw_a, voltage_cal_v, current_cal_a,
                   conductance_s, kappa_t_us_cm, kappa_25_us_cm, k25,
                   excitation_frequency_hz, excitation_amplitude_v, range_id,
                   compensation_model, alpha_per_c, calibration_id,
                   cell_constant_cm_inv, calibration_valid_until_utc,
                   quality_flags, status
            FROM raw_frames
            WHERE experiment_id = ?
            ORDER BY id ASC
            LIMIT ? OFFSET ?
            """,
            (experiment_id, limit, offset),
        ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            frame = dict(row)
            # SQLite 保留紧凑、向后兼容的 TEXT 列；API 同时给出结构化列表，
            # 历史调用方无需自行猜分隔符即可识别 DROPOUT/OUT_OF_RANGE。
            frame["quality_flags_list"] = [
                flag for flag in (frame.get("quality_flags") or "").split("|") if flag
            ]
            result.append(frame)
        return result


def count_frames(experiment_id: int) -> int:
    with _managed_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM raw_frames WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        return int(row["n"])


def export_csv(experiment_id: int) -> str:
    """导出该实验全部原始帧为 CSV 文本（Excel 可直接打开）。

    同时包含 Raw / Calibrated / Derived / Configuration / Trace / Quality 各层字段。
    """
    with _managed_conn() as conn:
        rows = conn.execute(
            """
            SELECT seq_no, timestamp_utc, monotonic_ms, t_seconds, schema_version,
                   sensor_path_id, sample_id,
                   legacy_ec_us_cm, temperature_raw_c,
                   voltage_raw_v, current_raw_a, voltage_cal_v, current_cal_a,
                   conductance_s, kappa_t_us_cm, kappa_25_us_cm,
                   excitation_frequency_hz, excitation_amplitude_v, range_id,
                   compensation_model, alpha_per_c, calibration_id,
                   cell_constant_cm_inv, calibration_valid_until_utc,
                   quality_flags, status
            FROM raw_frames
            WHERE experiment_id = ?
            ORDER BY id ASC
            """,
            (experiment_id,),
        ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "seq_no",
            "timestamp_utc",
            "monotonic_ms",
            "t_seconds",
            "schema_version",
            "sensor_path_id",
            "sample_id",
            "legacy_ec_us_cm",
            "temperature_raw_c",
            "voltage_raw_v",
            "current_raw_a",
            "voltage_cal_v",
            "current_cal_a",
            "conductance_s",
            "kappa_t_us_cm",
            "kappa_25_us_cm",
            "excitation_frequency_hz",
            "excitation_amplitude_v",
            "range_id",
            "compensation_model",
            "alpha_per_c",
            "calibration_id",
            "cell_constant_cm_inv",
            "calibration_valid_until_utc",
            "quality_flags",
            "status",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r["seq_no"],
                r["timestamp_utc"],
                r["monotonic_ms"],
                r["t_seconds"],
                r["schema_version"],
                r["sensor_path_id"],
                r["sample_id"],
                r["legacy_ec_us_cm"],
                r["temperature_raw_c"],
                r["voltage_raw_v"],
                r["current_raw_a"],
                r["voltage_cal_v"],
                r["current_cal_a"],
                r["conductance_s"],
                r["kappa_t_us_cm"],
                r["kappa_25_us_cm"],
                r["excitation_frequency_hz"],
                r["excitation_amplitude_v"],
                r["range_id"],
                r["compensation_model"],
                r["alpha_per_c"],
                r["calibration_id"],
                r["cell_constant_cm_inv"],
                r["calibration_valid_until_utc"],
                r["quality_flags"],
                r["status"],
            ]
        )
    return buf.getvalue()
