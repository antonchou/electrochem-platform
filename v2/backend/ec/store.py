"""SQLite 存储：测量、原始帧、标定。

- 帧表只存原始量（U/I/T、设备序号与时钟、质量标志），append-only：触发器禁止改删。
- 测量表存样品信息和参数快照（Kcell、所用标定、α、设备元数据）；只允许把 running 的测量
  结束一次（写状态、结束时间、判稳结果），样品与参数列永远不能改。
- 标定只插入。
- 迁移：PRAGMA user_version 记版本，一个版本一个事务，失败整体回滚。

所有方法都是同步的，调用方用 asyncio.to_thread 放到线程池；每次调用各开一个连接。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, NamedTuple, Sequence

_SCHEMA_V1 = """
CREATE TABLE calibrations (
    id                    INTEGER PRIMARY KEY,
    created_at            TEXT NOT NULL,
    cell_constant_per_cm  REAL NOT NULL CHECK (cell_constant_per_cm > 0),
    r2                    REAL,
    rsd_pct               REAL,
    operator              TEXT,
    cell_id               TEXT,
    lot                   TEXT,
    note                  TEXT
);
CREATE TABLE calibration_points (
    calibration_id          INTEGER NOT NULL REFERENCES calibrations(id),
    measurement_id          INTEGER NOT NULL REFERENCES measurements(id),
    standard_name           TEXT NOT NULL,
    standard_kappa25_us_cm  REAL NOT NULL CHECK (standard_kappa25_us_cm > 0),
    conductance25_s         REAL NOT NULL,
    deviation_pct           REAL,
    PRIMARY KEY (calibration_id, measurement_id)
);
CREATE TABLE measurements (
    id                       INTEGER PRIMARY KEY,
    sample_name              TEXT NOT NULL,
    concentration_mmol_l     REAL CHECK (concentration_mmol_l IS NULL OR concentration_mmol_l >= 0),
    note                     TEXT,
    status                   TEXT NOT NULL CHECK (status IN ('running', 'completed', 'aborted')),
    started_at               TEXT NOT NULL,
    ended_at                 TEXT,
    cell_constant_per_cm     REAL NOT NULL CHECK (cell_constant_per_cm > 0),
    calibration_id           INTEGER REFERENCES calibrations(id),
    alpha_per_c              REAL NOT NULL,
    device_kind              TEXT NOT NULL,
    device_id                TEXT,
    firmware_version         TEXT,
    range_id                 TEXT,
    excitation_frequency_hz  REAL,
    excitation_amplitude_v   REAL,
    qc                       TEXT
);
CREATE TABLE frames (
    measurement_id  INTEGER NOT NULL REFERENCES measurements(id),
    seq             INTEGER NOT NULL,
    t_s             REAL NOT NULL,
    timestamp_utc   TEXT NOT NULL,
    device_seq      INTEGER,
    device_ms       INTEGER,
    voltage_v       REAL,
    current_a       REAL,
    temperature_c   REAL,
    flags           TEXT,
    PRIMARY KEY (measurement_id, seq)
) WITHOUT ROWID;

CREATE TRIGGER frames_no_update BEFORE UPDATE ON frames
BEGIN SELECT RAISE(ABORT, 'frames are append-only'); END;
CREATE TRIGGER frames_no_delete BEFORE DELETE ON frames
BEGIN SELECT RAISE(ABORT, 'frames are append-only'); END;
CREATE TRIGGER calibrations_no_update BEFORE UPDATE ON calibrations
BEGIN SELECT RAISE(ABORT, 'calibrations are append-only'); END;
CREATE TRIGGER calibrations_no_delete BEFORE DELETE ON calibrations
BEGIN SELECT RAISE(ABORT, 'calibrations are append-only'); END;
CREATE TRIGGER calibration_points_no_update BEFORE UPDATE ON calibration_points
BEGIN SELECT RAISE(ABORT, 'calibrations are append-only'); END;
CREATE TRIGGER calibration_points_no_delete BEFORE DELETE ON calibration_points
BEGIN SELECT RAISE(ABORT, 'calibrations are append-only'); END;
CREATE TRIGGER measurements_no_delete BEFORE DELETE ON measurements
BEGIN SELECT RAISE(ABORT, 'measurements cannot be deleted'); END;
CREATE TRIGGER measurements_finish_once BEFORE UPDATE ON measurements WHEN OLD.status <> 'running'
BEGIN SELECT RAISE(ABORT, 'finished measurements are immutable'); END;
CREATE TRIGGER measurements_fixed_columns BEFORE UPDATE OF
    id, sample_name, concentration_mmol_l, note, started_at, cell_constant_per_cm, calibration_id,
    alpha_per_c, device_kind, device_id, firmware_version, range_id, excitation_frequency_hz,
    excitation_amplitude_v ON measurements
BEGIN SELECT RAISE(ABORT, 'measurement sample and parameters are immutable'); END;
"""

# 第 N 个元素把库从版本 N 升到 N+1。只追加，不修改已发布的迁移。
MIGRATIONS: tuple[str, ...] = (_SCHEMA_V1,)
SCHEMA_VERSION = len(MIGRATIONS)


class FrameRow(NamedTuple):
    measurement_id: int
    seq: int
    t_s: float
    timestamp_utc: str
    device_seq: int | None
    device_ms: int | None
    voltage_v: float | None
    current_a: float | None
    temperature_c: float | None
    flags: str | None


_MEASUREMENT_PARAMS = (
    "sample_name",
    "concentration_mmol_l",
    "note",
    "started_at",
    "cell_constant_per_cm",
    "calibration_id",
    "alpha_per_c",
    "device_kind",
    "device_id",
    "firmware_version",
    "range_id",
    "excitation_frequency_hz",
    "excitation_amplitude_v",
)

_MEASUREMENT_SELECT = """
SELECT m.*,
       (SELECT MAX(seq) FROM frames f WHERE f.measurement_id = m.id) AS frame_count,
       (SELECT t_s FROM frames f WHERE f.measurement_id = m.id ORDER BY seq DESC LIMIT 1) AS duration_s
FROM measurements m
"""


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            migrate(conn)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    # ---- 测量 ----
    def create_measurement(self, params: dict[str, Any]) -> int:
        columns = ", ".join(_MEASUREMENT_PARAMS)
        marks = ", ".join("?" for _ in _MEASUREMENT_PARAMS)
        with self._transaction() as conn:
            cur = conn.execute(
                f"INSERT INTO measurements (status, {columns}) VALUES ('running', {marks})",
                [params.get(name) for name in _MEASUREMENT_PARAMS],
            )
            return int(cur.lastrowid)

    def finish_measurement(
        self, measurement_id: int, status: str, ended_at: str, qc: dict[str, Any] | None
    ) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE measurements SET status = ?, ended_at = ?, qc = ? WHERE id = ? AND status = 'running'",
                (status, ended_at, json.dumps(qc, allow_nan=False) if qc is not None else None, measurement_id),
            )

    def running_measurement_ids(self) -> list[int]:
        with self._connect() as conn:
            return [row[0] for row in conn.execute("SELECT id FROM measurements WHERE status = 'running'")]

    def get_measurement(self, measurement_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(_MEASUREMENT_SELECT + " WHERE m.id = ?", (measurement_id,)).fetchone()
        return _measurement(row) if row else None

    def list_measurements(self, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(_MEASUREMENT_SELECT + " ORDER BY m.id DESC LIMIT ? OFFSET ?", (limit, offset))
            return [_measurement(row) for row in rows]

    # ---- 帧 ----
    def insert_frames(self, rows: Sequence[FrameRow]) -> None:
        if not rows:
            return
        with self._transaction() as conn:
            conn.executemany("INSERT INTO frames VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def frames(self, measurement_id: int, last_seconds: float | None = None) -> list[FrameRow]:
        """一次测量的帧（按序号）；给出 last_seconds 时只取最后这么多秒（t_s 单调不减）。"""
        sql = "SELECT * FROM frames WHERE measurement_id = ?"
        args: list[Any] = [measurement_id]
        if last_seconds is not None:
            sql += " AND t_s >= (SELECT MAX(t_s) FROM frames WHERE measurement_id = ?) - ?"
            args += [measurement_id, last_seconds]
        with self._connect() as conn:
            return [FrameRow(*row) for row in conn.execute(sql + " ORDER BY seq", args)]

    # ---- 标定 ----
    def create_calibration(self, calibration: dict[str, Any], points: Sequence[dict[str, Any]]) -> int:
        with self._transaction() as conn:
            cur = conn.execute(
                "INSERT INTO calibrations (created_at, cell_constant_per_cm, r2, rsd_pct, operator, cell_id, lot, note)"
                " VALUES (:created_at, :cell_constant_per_cm, :r2, :rsd_pct, :operator, :cell_id, :lot, :note)",
                calibration,
            )
            calibration_id = int(cur.lastrowid)
            conn.executemany(
                "INSERT INTO calibration_points (calibration_id, measurement_id, standard_name,"
                " standard_kappa25_us_cm, conductance25_s, deviation_pct)"
                " VALUES (:calibration_id, :measurement_id, :standard_name, :standard_kappa25_us_cm,"
                " :conductance25_s, :deviation_pct)",
                [{**point, "calibration_id": calibration_id} for point in points],
            )
            return calibration_id

    def list_calibrations(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            calibrations = [dict(row) for row in conn.execute(
                "SELECT * FROM calibrations ORDER BY id DESC LIMIT ?", (limit,)
            )]
            for cal in calibrations:
                cal["points"] = [dict(row) for row in conn.execute(
                    "SELECT p.*, m.sample_name FROM calibration_points p"
                    " JOIN measurements m ON m.id = p.measurement_id"
                    " WHERE p.calibration_id = ? ORDER BY p.standard_kappa25_us_cm",
                    (cal["id"],),
                )]
        return calibrations

    def latest_calibration(self) -> dict[str, Any] | None:
        calibrations = self.list_calibrations(limit=1)
        return calibrations[0] if calibrations else None


def migrate(conn: sqlite3.Connection) -> int:
    """把库升到最新版本，返回升级前的版本。新库与旧库最终结构一致。"""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"database schema v{current} is newer than this program (v{SCHEMA_VERSION})")
    for version in range(current, SCHEMA_VERSION):
        try:
            # executescript 会先提交挂起的事务，所以 BEGIN/COMMIT 写进脚本本身
            conn.executescript(f"BEGIN;\n{MIGRATIONS[version]}\nPRAGMA user_version = {version + 1};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return current


def _measurement(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["qc"] = json.loads(out["qc"]) if out["qc"] else None
    out["frame_count"] = out["frame_count"] or 0
    return out
