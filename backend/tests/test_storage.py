"""SQLite 存储层测试：生命周期、append-only 约束、样品汇总、CSV 导出。"""

import logging
import sqlite3

import pytest

from app import storage

SENSOR = "MOCK_EC_IV"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "storage_test.db"))
    from app import storage

    storage.init_db()
    yield storage


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


# ---------------- 帧写入、导出与 QC 写回 ----------------


def _frame(experiment_id: int, sensor_path_id: str) -> dict:
    return {
        "experiment_id": experiment_id,
        "sample_id": "SAME",
        "sensor_path_id": sensor_path_id,
        "seq_no": 1,
        "timestamp_utc": "2026-08-21T00:00:00Z",
        "monotonic_ms": 1,
        "t_seconds": 0.1,
        "ec_raw": 100.0,
        "temperature_raw": 25.0,
        "k25": None,
        "quality_flags": None,
        "status": "running",
    }


def test_frame_count_isolated_by_sensor_path(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "paths.db"))
    storage.init_db()
    exp_id = storage.create_experiment_with_sample("EXP-PATHS", "paths", "SAME", "NARROW")

    storage.insert_frames([_frame(exp_id, "WIDE")])

    samples = {item["sensor_path_id"]: item["frame_count"] for item in storage.get_samples(exp_id)}
    assert samples == {"WIDE": 1, "NARROW": 0}


def test_insert_frames_upserts_missing_sample(tmp_path, monkeypatch):
    """P0-2：帧写入时样品行不存在则补建并累计 frame_count。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "upsert-count.db"))
    storage.init_db()
    exp_id = storage.create_experiment_with_sample("EXP-UPSERT", "upsert", "SAME", "NARROW")
    storage.insert_frames([_frame(exp_id, "WIDE")])
    storage.insert_frames([_frame(exp_id, "WIDE")])
    samples = {item["sensor_path_id"]: item["frame_count"] for item in storage.get_samples(exp_id)}
    assert samples == {"NARROW": 0, "WIDE": 2}


def test_experiment_and_sample_creation_rolls_back_together(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "atomic.db"))
    storage.init_db()

    with pytest.raises(sqlite3.IntegrityError):
        storage.create_experiment_with_sample(
            "EXP-ATOMIC",
            "atomic",
            None,  # type: ignore[arg-type]  # 强制触发 samples.sample_id NOT NULL
            "WIDE",
        )

    assert storage.list_experiments() == []


def test_insert_frames_skips_malformed_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t05.db"))
    storage.init_db()
    exp_id = storage.create_experiment_with_sample("EXP-T05", "malformed rows", "S1", "MOCK_EC_IV")

    good = {
        "experiment_id": exp_id,
        "sample_id": "S1",
        "sensor_path_id": "MOCK_EC_IV",
        "t_seconds": 0.1,
        "ec_raw": 100.0,
        "temperature_raw": 25.0,
    }
    missing_exp = {k: v for k, v in good.items() if k != "experiment_id"}
    missing_temp = {k: v for k, v in good.items() if k != "temperature_raw"}

    # 修复前：KeyError / IntegrityError → 整批失败 → 持久化永久降级
    storage.insert_frames([good, missing_exp, missing_temp])

    frames = storage.get_frames(exp_id)
    assert len(frames) == 1
    assert frames[0]["t_seconds"] == 0.1
    samples = {s["sample_id"]: s["frame_count"] for s in storage.get_samples(exp_id)}
    assert samples == {"S1": 1}


def test_insert_frames_all_malformed_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t05b.db"))
    storage.init_db()
    storage.insert_frames([{"sample_id": "X", "t_seconds": 0.0}])  # 不抛异常即可


def test_storage_export_json_full_content(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t07.db"))
    storage.init_db()
    exp_id = storage.create_experiment_with_sample("EXP-T07", "export", "S1", "MOCK_EC_IV")
    storage.insert_frames(
        [
            {
                "experiment_id": exp_id,
                "sample_id": "S1",
                "sensor_path_id": "MOCK_EC_IV",
                "t_seconds": float(i),
                "ec_raw": 100.0 + i,
                "temperature_raw": 25.0,
            }
            for i in range(5)
        ]
    )
    import json

    payload = json.loads(storage.export_json(exp_id))
    assert payload["id"] == exp_id
    assert payload["experiment_id"] == "EXP-T07"
    assert payload["truncated"] is False
    assert payload["frame_count_total"] == 5
    assert len(payload["frames"]) == 5


def test_storage_export_json_missing_raises_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "t07b.db"))
    storage.init_db()
    with pytest.raises(LookupError):
        storage.export_json(99999)


def test_insert_frames_malformed_drop_is_logged(tmp_path, monkeypatch, caplog):
    """P2-4：畸形帧被丢弃时必须留 warning（带条数），不得静默消失。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "r2-log.db"))
    storage.init_db()
    exp_id = storage.create_experiment_with_sample("EXP-R2", "logging", "S1", "MOCK_EC_IV")
    good = {
        "experiment_id": exp_id,
        "sample_id": "S1",
        "sensor_path_id": "MOCK_EC_IV",
        "t_seconds": 0.1,
        "ec_raw": 100.0,
        "temperature_raw": 25.0,
    }
    malformed = {k: v for k, v in good.items() if k != "temperature_raw"}

    with caplog.at_level(logging.WARNING, logger="app.storage"):
        storage.insert_frames([good, malformed, dict(malformed)])

    assert any(
        "skipped 2 malformed frame" in rec.message for rec in caplog.records
    ), [r.getMessage() for r in caplog.records]
    assert len(storage.get_frames(exp_id)) == 1


def test_update_sample_qc_overwrites_stale_representative(tmp_path, monkeypatch):
    """续跑后第二次停止判 FAIL：不得留着第一次 PASS 的代表值（界面会显示 FAIL + 代表值）。"""
    monkeypatch.setenv("EC_DB_PATH", str(tmp_path / "qc.db"))
    storage.init_db()
    exp = storage.create_experiment_with_sample(
        experiment_id="EXP-QC", title="t", sample_id="S", sensor_path_id=SENSOR
    )
    common = {"experiment_id": exp, "sample_id": "S", "sensor_path_id": SENSOR}
    storage.update_sample_qc(
        **common, qc_status="PASS", qc_reason="stable", representative_value=1413.0,
        k25_median=1413.0, k25_mean=1413.0, k25_sd=0.1,
    )
    storage.update_sample_qc(
        **common, qc_status="FAIL", qc_reason="hard_quality_flag", representative_value=None,
        k25_median=1500.0, k25_mean=1499.0, k25_sd=9.0,
    )
    s = storage.get_samples(exp)[0]
    assert s["qc_status"] == "FAIL"
    assert s["representative_value"] is None
    assert s["k25_median"] == 1500.0
