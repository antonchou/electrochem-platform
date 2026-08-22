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
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _conn() -> sqlite3.Connection:
    db = _db_path()
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """建表（幂等）。应用启动时调用。"""
    with _conn() as conn:
        conn.executescript(SCHEMA)


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


# ---------------- 原始帧 ----------------

def insert_frames(frames: List[Dict[str, Any]]) -> None:
    """批量写入原始帧（append-only），并累加对应样品的 frame_count（P2-4）。"""
    if not frames:
        return
    with _conn() as conn:
        conn.executemany(
            """
            INSERT INTO raw_frames
                (experiment_id, sample_id, sensor_path_id, seq_no, timestamp_utc,
                 monotonic_ms, t_seconds, ec_raw, temperature_raw, k25, quality_flags, status)
            VALUES (:experiment_id, :sample_id, :sensor_path_id, :seq_no, :timestamp_utc,
                    :monotonic_ms, :t_seconds, :ec_raw, :temperature_raw, :k25, :quality_flags, :status)
            """,
            frames,
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
            """
            SELECT id, sample_id, sensor_path_id, seq_no, timestamp_utc,
                   monotonic_ms, t_seconds, ec_raw, temperature_raw, k25, quality_flags, status
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
    """导出该实验全部原始帧为 CSV 文本（Excel 可直接打开）。"""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT seq_no, timestamp_utc, monotonic_ms, t_seconds,
                   sensor_path_id, sample_id, ec_raw, temperature_raw, k25, quality_flags, status
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
            "sensor_path_id",
            "sample_id",
            "ec_raw_us_cm",
            "temperature_raw_c",
            "k25_us_cm",
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
                r["sensor_path_id"],
                r["sample_id"],
                r["ec_raw"],
                r["temperature_raw"],
                r["k25"],
                r["quality_flags"],
                r["status"],
            ]
        )
    return buf.getvalue()
