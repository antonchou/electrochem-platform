"""SQLite 存储层（Phase 7 数据存储与导出）。

- experiments / samples / raw_frames / calibration_records 四张核心表
- raw_frames 通过触发器强制 append-only：不允许 UPDATE / DELETE，保证原始数据不可篡改
- 所有 DAO 均同步执行，由调用方通过 asyncio.to_thread 放到线程池，避免阻塞事件循环
- DB 路径可用环境变量 EC_DB_PATH 覆盖（测试用临时库）
"""

from __future__ import annotations

import csv
import datetime
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

# ---------------------------------------------------------------------------
# 版本化迁移框架（2026-08-23 建立，V2 回归事故后）
#
# 纪律（见 docs/V2-regression-postmortem.md 教训）：
# - 一个迁移一个事务，失败整体回滚，绝不出现半迁移状态
# - 迁移内禁用 executescript（会隐式提交），全部用 execute 逐条执行
# - 新增迁移必须同时提供「旧库升级」测试（tests/test_migrations.py）
# - SCHEMA 保持为 version 1（V1 baseline）结构；新库直建即 version 1
# ---------------------------------------------------------------------------
SCHEMA_VERSION = 6
DEFAULT_CALIBRATION_ID = "MOCK-KCELL-1.0"
DEFAULT_DERIVED_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "derived"


def _migrate_v2(conn: sqlite3.Connection) -> None:
    """v2：raw_frames 增加 I–V 计算链字段（REQ-M-001 软件侧）。

    - voltage_raw_v / current_raw_a：原始 U/I（不可变，替代 ec_raw 语义）
    - conductance_s / kappa_t_us_cm / kappa_25_us_cm：可重算结果
    - 仅加可空列，旧数据/旧前端不受影响；不触碰 append-only 触发器。
    """
    conn.execute("ALTER TABLE raw_frames ADD COLUMN voltage_raw_v REAL")
    conn.execute("ALTER TABLE raw_frames ADD COLUMN current_raw_a REAL")
    conn.execute("ALTER TABLE raw_frames ADD COLUMN conductance_s REAL")
    conn.execute("ALTER TABLE raw_frames ADD COLUMN kappa_t_us_cm REAL")
    conn.execute("ALTER TABLE raw_frames ADD COLUMN kappa_25_us_cm REAL")


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """v3：samples 增加判稳与 QC 字段（REQ-D-003）。

    - qc_status / qc_reason：PASS/WARN/FAIL 判定与失败原因
    - representative_value：稳定段代表值
    - qc_checked_at_utc：判稳执行时间
    - 仅加可空列，旧数据不受影响。
    """
    conn.execute("ALTER TABLE samples ADD COLUMN qc_status TEXT")
    conn.execute("ALTER TABLE samples ADD COLUMN qc_reason TEXT")
    conn.execute("ALTER TABLE samples ADD COLUMN representative_value REAL")
    conn.execute("ALTER TABLE samples ADD COLUMN qc_checked_at_utc TEXT")


def _migrate_v4(conn: sqlite3.Connection) -> None:
    """v4：raw_frames.ec_raw 解除 NOT NULL（真实原始帧可无 ec）。

    CV 数据电压可为负 -> 计算链拒绝 -> ec 为 NULL；V1 遗留 ec_raw NOT NULL
    约束不再成立。按 V2 教训 #2 重建表：同一事务内 DROP/CREATE/COPY/RENAME
    + 重建索引/触发器 + 行数校验。
    """
    old_count = conn.execute("SELECT COUNT(*) FROM raw_frames").fetchone()[0]
    conn.execute(
        """
        CREATE TABLE raw_frames_v4 (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id  INTEGER NOT NULL REFERENCES experiments(id),
            sample_id      TEXT,
            sensor_path_id TEXT NOT NULL,
            seq_no         INTEGER,
            timestamp_utc  TEXT,
            monotonic_ms   INTEGER,
            t_seconds      REAL,
            ec_raw         REAL,
            temperature_raw REAL NOT NULL,
            k25            REAL,
            quality_flags  TEXT,
            status         TEXT,
            voltage_raw_v  REAL,
            current_raw_a  REAL,
            conductance_s  REAL,
            kappa_t_us_cm  REAL,
            kappa_25_us_cm REAL,
            inserted_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
        """
    )
    conn.execute(
        "INSERT INTO raw_frames_v4 "
        "(id, experiment_id, sample_id, sensor_path_id, seq_no, timestamp_utc, monotonic_ms, "
        "t_seconds, ec_raw, temperature_raw, k25, quality_flags, status, voltage_raw_v, "
        "current_raw_a, conductance_s, kappa_t_us_cm, kappa_25_us_cm, inserted_at_utc) "
        "SELECT id, experiment_id, sample_id, sensor_path_id, seq_no, timestamp_utc, monotonic_ms, "
        "t_seconds, ec_raw, temperature_raw, k25, quality_flags, status, voltage_raw_v, "
        "current_raw_a, conductance_s, kappa_t_us_cm, kappa_25_us_cm, inserted_at_utc "
        "FROM raw_frames"
    )
    new_count = conn.execute("SELECT COUNT(*) FROM raw_frames_v4").fetchone()[0]
    if new_count != old_count:
        raise RuntimeError(f"v4 row count mismatch: old={old_count} new={new_count}")
    conn.execute("DROP TABLE raw_frames")
    conn.execute("ALTER TABLE raw_frames_v4 RENAME TO raw_frames")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_frames_exp ON raw_frames(experiment_id, id)")
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS block_raw_frames_update "
        "BEFORE UPDATE ON raw_frames "
        "BEGIN SELECT RAISE(ABORT, 'raw_frames is append-only: UPDATE is not allowed'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS block_raw_frames_delete "
        "BEFORE DELETE ON raw_frames "
        "BEGIN SELECT RAISE(ABORT, 'raw_frames is append-only: DELETE is not allowed'); END"
    )


def _migrate_v5(conn: sqlite3.Connection) -> None:
    """v5：raw_frames 落协议溯源与激励/校准元数据（REQ-C-001 软件侧）。

    - schema_version / device_id / firmware_version / range_id：设备协议
    - calibration_id：关联 calibration_records
    - excitation_frequency_hz / excitation_amplitude_v：激励设置
    - compensation_model：温补模型标识（当前 linear_alpha）
    - 仅加可空列，不重建表、不触碰 append-only 触发器。
    """
    conn.execute("ALTER TABLE raw_frames ADD COLUMN schema_version INTEGER")
    conn.execute("ALTER TABLE raw_frames ADD COLUMN device_id TEXT")
    conn.execute("ALTER TABLE raw_frames ADD COLUMN firmware_version TEXT")
    conn.execute("ALTER TABLE raw_frames ADD COLUMN range_id TEXT")
    conn.execute("ALTER TABLE raw_frames ADD COLUMN calibration_id TEXT")
    conn.execute("ALTER TABLE raw_frames ADD COLUMN excitation_frequency_hz REAL")
    conn.execute("ALTER TABLE raw_frames ADD COLUMN excitation_amplitude_v REAL")
    conn.execute("ALTER TABLE raw_frames ADD COLUMN compensation_model TEXT")


def _migrate_v6(conn: sqlite3.Connection) -> None:
    """v6：拟合报告入库（REQ-F-001/002 软件侧）。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fit_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER NOT NULL REFERENCES experiments(id),
            sample_id TEXT,
            x_axis TEXT NOT NULL,
            model TEXT NOT NULL,
            label TEXT,
            params_json TEXT NOT NULL,
            r2 REAL,
            rmse REAL,
            mae REAL,
            aicc REAL,
            loocv_rmse REAL,
            n INTEGER,
            x_min REAL,
            x_max REAL,
            extrapolation_forbidden INTEGER NOT NULL DEFAULT 1,
            residual_max_abs REAL,
            param_ci_json TEXT,
            derived_path TEXT,
            created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_fit_results_exp ON fit_results(experiment_id, id)"
    )


# 迁移注册表：{版本号: 迁移函数(conn)}，版本号单调递增、必须 <= SCHEMA_VERSION
MIGRATIONS: Dict[int, Any] = {
    2: _migrate_v2,
    3: _migrate_v3,
    4: _migrate_v4,
    5: _migrate_v5,
    6: _migrate_v6,
}


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
    ec_raw         REAL NOT NULL,
    temperature_raw REAL NOT NULL,
    k25            REAL,
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


def _connect() -> sqlite3.Connection:
    db = _db_path()
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, always close (P2-1)."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """幂等初始化：全新库建 V1 结构并标记 version 1；历史库逐步迁移到 SCHEMA_VERSION。

    迁移纪律（V2 回归事故教训）：
    - 每个迁移独立事务，失败整体回滚，绝不留下半迁移状态；
    - 迁移内禁用 executescript（会隐式提交），全部用 execute 逐条执行；
    - 新增迁移必须同时提供「旧库升级」测试（tests/test_migrations.py）。
    """
    conn = _connect()
    try:
        _apply_migrations(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """确保 schema_migrations 表存在，并把库推进到 SCHEMA_VERSION。

    新库与旧库都从 V1 结构出发，逐版本迁移到 SCHEMA_VERSION，保证两路径结果一致
    （V2 事故教训 #3：新库直建 vs 旧库升级到同一版本后结构必须相同）。
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " applied_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
        ")"
    )
    conn.executescript(SCHEMA)  # 幂等：基础表/索引/触发器 IF NOT EXISTS
    applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    if 1 not in applied:
        # 全新库 或 未迁移的历史库：V1 结构即 SCHEMA（已在上方 executescript 建立）
        conn.execute("INSERT INTO schema_migrations (version) VALUES (1)")
        # 提交基线标记，清空 sqlite3 legacy 模式自动开启的事务，
        # 否则后续 _run_migration 的显式 BEGIN 会报 nested transaction。
        conn.commit()
        applied.add(1)
    for version in sorted(MIGRATIONS):
        if version in applied or version > SCHEMA_VERSION:
            continue
        _run_migration(conn, version)


def _run_migration(conn: sqlite3.Connection, version: int) -> None:
    """以独立事务执行一个迁移：成功则记录版本并提交，失败整体回滚。"""
    conn.execute("BEGIN")
    try:
        MIGRATIONS[version](conn)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


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
    with _conn() as conn:
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
    with _conn() as conn:
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


def reopen_experiment(experiment_id: int) -> bool:
    """Pause 后续跑：清 ended_at，状态改回 running。仅允许从 stopped 恢复。"""
    with _conn() as conn:
        cur = conn.execute(
            """
            UPDATE experiments
               SET status = 'running', ended_at_utc = NULL
             WHERE id = ? AND status = 'stopped'
            """,
            (experiment_id,),
        )
        return cur.rowcount > 0


def finish_experiment(experiment_id: int, status: str = "stopped") -> None:
    import datetime

    if status not in FINAL_EXPERIMENT_STATUSES:
        allowed = ", ".join(sorted(FINAL_EXPERIMENT_STATUSES))
        raise ValueError(f"invalid terminal experiment status {status!r}; expected one of: {allowed}")
    ended = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with _conn() as conn:
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

    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO samples
                (experiment_id, sample_id, sensor_path_id, concentration_mmol_l,
                 composition, preparation_record_id, measured_at_utc,
                 k25_median, k25_mean, k25_sd, frame_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(experiment_id, sample_id, sensor_path_id) DO UPDATE SET
                frame_count = frame_count + excluded.frame_count,
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


# ---------------- 判稳与 QC（REQ-D-003） ----------------

def update_sample_qc(
    experiment_id: int,
    sample_id: str,
    sensor_path_id: str,
    *,
    qc_status: Optional[str],
    qc_reason: Optional[str],
    representative_value: Optional[float] = None,
    k25_median: Optional[float] = None,
    k25_mean: Optional[float] = None,
    k25_sd: Optional[float] = None,
) -> None:
    """把判稳/QC 结果写入 samples（REQ-D-003）。仅更新已存在行；不存在则跳过。"""
    import datetime

    checked = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with _conn() as conn:
        conn.execute(
            """
            UPDATE samples SET
                qc_status            = ?,
                qc_reason            = ?,
                representative_value = COALESCE(?, representative_value),
                k25_median           = COALESCE(?, k25_median),
                k25_mean             = COALESCE(?, k25_mean),
                k25_sd               = COALESCE(?, k25_sd),
                qc_checked_at_utc    = ?
            WHERE experiment_id = ? AND sample_id = ? AND sensor_path_id = ?
            """,
            (
                qc_status,
                qc_reason,
                representative_value,
                k25_median,
                k25_mean,
                k25_sd,
                checked,
                experiment_id,
                sample_id,
                sensor_path_id,
            ),
        )


# ---------------- 原始帧 ----------------

# raw_frames 全列（v2 I–V 计算链 + v5 协议/校准/激励元数据）。
# insert_frames 用命名参数；旧帧缺字段时自动补 None，避免 sqlite3 报错。
_FRAME_COLUMNS = [
    "experiment_id",
    "sample_id",
    "sensor_path_id",
    "seq_no",
    "timestamp_utc",
    "monotonic_ms",
    "t_seconds",
    "ec_raw",
    "temperature_raw",
    "k25",
    "quality_flags",
    "status",
    "voltage_raw_v",
    "current_raw_a",
    "conductance_s",
    "kappa_t_us_cm",
    "kappa_25_us_cm",
    "schema_version",
    "device_id",
    "firmware_version",
    "range_id",
    "calibration_id",
    "excitation_frequency_hz",
    "excitation_amplitude_v",
    "compensation_model",
]
_INSERT_FRAMES_SQL = (
    "INSERT INTO raw_frames ("
    + ", ".join(_FRAME_COLUMNS)
    + ") VALUES ("
    + ", ".join(f":{col}" for col in _FRAME_COLUMNS)
    + ")"
)
_FRAME_READ_SQL = "id, " + ", ".join(_FRAME_COLUMNS)


def insert_frames(frames: List[Dict[str, Any]]) -> None:
    """批量写入原始帧（append-only），并累加对应样品的 frame_count（P2-4）。

    v2 之后 raw_frames 带 I–V 计算链列；旧帧（无新字段）插入时这些列保持 NULL。
    对缺失字段自动补 None，兼容历史测试/旧调用方的简化帧。
    """
    if not frames:
        return
    rows = [{col: f.get(col) for col in _FRAME_COLUMNS} for f in frames]
    measured = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with _conn() as conn:
        conn.executemany(_INSERT_FRAMES_SQL, rows)
        # 必须按完整样品链路聚合；同一样品编号可能同时走 WIDE/NARROW 等不同通道。
        counts: Dict[tuple[int, str, str], int] = {}
        for f in frames:
            sample_id = f.get("sample_id")
            if sample_id is None:
                continue
            sensor_path_id = f.get("sensor_path_id") or ""
            key = (f["experiment_id"], sample_id, sensor_path_id)
            counts[key] = counts.get(key, 0) + 1
        for (exp_id, sample_id, sensor_path_id), n in counts.items():
            conn.execute(
                """
                INSERT INTO samples
                    (experiment_id, sample_id, sensor_path_id, measured_at_utc, frame_count)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(experiment_id, sample_id, sensor_path_id) DO UPDATE SET
                    frame_count = frame_count + excluded.frame_count
                """,
                (exp_id, sample_id, sensor_path_id, measured, n),
            )


# ---------------- 查询 ----------------

def list_experiments() -> List[Dict[str, Any]]:
    with _conn() as conn:
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
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM experiments WHERE id = ?", (experiment_id,)
        ).fetchone()
        return dict(row) if row else None


def get_samples(experiment_id: int) -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM samples WHERE experiment_id = ? ORDER BY id", (experiment_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_frames(experiment_id: int, *, limit: int = 500) -> List[Dict[str, Any]]:
    """Return the newest `limit` frames in chronological order (oldest→newest).

    Used by stop-time QC so the stability window is the experiment tail, not a
    prefix of the first 100k rows.
    """
    if not 1 <= limit <= 1_000_000:
        raise ValueError("limit must be between 1 and 1000000")
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT {_FRAME_READ_SQL}
            FROM raw_frames
            WHERE experiment_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (experiment_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


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
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT {_FRAME_READ_SQL}
            FROM raw_frames
            WHERE experiment_id = ?
            ORDER BY id ASC
            LIMIT ? OFFSET ?
            """,
            (experiment_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def count_frames(experiment_id: int) -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM raw_frames WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        return int(row["n"])


def export_csv(experiment_id: int) -> str:
    """导出该实验全部原始帧为 CSV 文本（Excel 可直接打开）。

    κ25 规范列名为 kappa_25_us_cm；k25_us_cm 为兼容别名。v5 起含协议溯源与激励/校准列。
    """
    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT {_FRAME_READ_SQL}
            FROM raw_frames
            WHERE experiment_id = ?
            ORDER BY id ASC
            """,
            (experiment_id,),
        ).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    # 规范名 kappa_25_us_cm；k25_us_cm 为兼容别名（与库内 k25 列对应）。
    writer.writerow(
        [
            "seq_no",
            "timestamp_utc",
            "monotonic_ms",
            "t_seconds",
            "sensor_path_id",
            "sample_id",
            "ec_raw_us_cm",
            "temperature_raw_c",
            "k25_us_cm",
            "quality_flags",
            "status",
            "voltage_raw_v",
            "current_raw_a",
            "conductance_s",
            "kappa_t_us_cm",
            "kappa_25_us_cm",
            "schema_version",
            "device_id",
            "firmware_version",
            "range_id",
            "calibration_id",
            "excitation_frequency_hz",
            "excitation_amplitude_v",
            "compensation_model",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r["seq_no"],
                r["timestamp_utc"],
                r["monotonic_ms"],
                r["t_seconds"],
                r["sensor_path_id"],
                r["sample_id"],
                r["ec_raw"],
                r["temperature_raw"],
                r["k25"],
                r["quality_flags"],
                r["status"],
                r["voltage_raw_v"],
                r["current_raw_a"],
                r["conductance_s"],
                r["kappa_t_us_cm"],
                r["kappa_25_us_cm"],
                r["schema_version"],
                r["device_id"],
                r["firmware_version"],
                r["range_id"],
                r["calibration_id"],
                r["excitation_frequency_hz"],
                r["excitation_amplitude_v"],
                r["compensation_model"],
            ]
        )
    return buf.getvalue()


# ---------------- 校准记录（REQ-C-001 软件侧） ----------------

def insert_calibration_record(
    *,
    calibration_id: str,
    experiment_id: Optional[int] = None,
    sensor_path_id: Optional[str] = None,
    mode: Optional[str] = None,
    standard: Optional[str] = None,
    lot: Optional[str] = None,
    coeff_value: Optional[float] = None,
    coeff_json: Optional[Dict[str, Any]] = None,
) -> int:
    """写入一条校准记录；同一 calibration_id 可被多条实验引用。"""
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO calibration_records
                (experiment_id, calibration_id, sensor_path_id, mode, standard, lot,
                 coeff_value, coeff_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                experiment_id,
                calibration_id,
                sensor_path_id,
                mode,
                standard,
                lot,
                coeff_value,
                json.dumps(coeff_json, ensure_ascii=False) if coeff_json is not None else None,
            ),
        )
        return int(cur.lastrowid)


def get_calibration_records(experiment_id: int) -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM calibration_records
             WHERE experiment_id = ?
             ORDER BY id
            """,
            (experiment_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------- 拟合报告（REQ-F-001/002） ----------------

def insert_fit_results(
    experiment_id: int,
    x_axis: str,
    models: List[Dict[str, Any]],
    *,
    sample_id: Optional[str] = None,
    derived_path: Optional[str] = None,
) -> List[int]:
    """Persist one fit run (one row per model)."""
    ids: List[int] = []
    with _conn() as conn:
        conn.execute(
            "DELETE FROM fit_results WHERE experiment_id = ? AND x_axis = ?",
            (experiment_id, x_axis),
        )
        for item in models:
            cur = conn.execute(
                """
                INSERT INTO fit_results
                    (experiment_id, sample_id, x_axis, model, label, params_json,
                     r2, rmse, mae, aicc, loocv_rmse, n, x_min, x_max,
                     extrapolation_forbidden, residual_max_abs, param_ci_json, derived_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment_id,
                    sample_id,
                    x_axis,
                    item.get("model"),
                    item.get("label"),
                    json.dumps(item.get("params") or {}, ensure_ascii=False),
                    item.get("r2"),
                    item.get("rmse"),
                    item.get("mae"),
                    item.get("aicc"),
                    item.get("loocv_rmse"),
                    item.get("n"),
                    item.get("x_min"),
                    item.get("x_max"),
                    1 if item.get("extrapolation_forbidden", True) else 0,
                    item.get("residual_max_abs"),
                    json.dumps(item.get("param_ci"), ensure_ascii=False)
                    if item.get("param_ci") is not None
                    else None,
                    derived_path,
                ),
            )
            ids.append(int(cur.lastrowid))
    return ids


def get_fit_results(experiment_id: int) -> List[Dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM fit_results
             WHERE experiment_id = ?
             ORDER BY id
            """,
            (experiment_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def write_fit_report(experiment_id: int, payload: Dict[str, Any]) -> str:
    """Write a derived JSON report under data/derived/ (gitignored). Overwrites per experiment+axis."""
    root = Path(os.environ.get("EC_DERIVED_DIR", str(DEFAULT_DERIVED_DIR)))
    root.mkdir(parents=True, exist_ok=True)
    axis = str(payload.get("x_axis") or "time")
    path = root / f"experiment_{experiment_id}_fit_{axis}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
