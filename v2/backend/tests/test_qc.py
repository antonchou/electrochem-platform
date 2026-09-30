import random

import pytest

from ec.qc import QcConfig, QcPoint, assess

CFG = QcConfig(window_s=30.0, min_points=10)


def series(values, dt=1.0, temps=None, flags=None):
    return [
        QcPoint(i * dt, v, 25.0 if temps is None else temps[i], () if flags is None else flags[i])
        for i, v in enumerate(values)
    ]


def noisy(mean, rel_sd, n=40, seed=1):
    rnd = random.Random(seed)
    return [mean * (1 + rnd.gauss(0, rel_sd)) for _ in range(n)]


def test_stable_series_passes_with_window_mean_as_representative():
    result = assess(series(noisy(1413.0, 0.001)), CFG)
    assert result.verdict == "PASS", result.reasons
    assert result.representative_kappa25 == pytest.approx(1413.0, rel=0.002)
    assert result.n_valid == 31  # t = 9..39，窗口 30 s 含两端
    assert result.window_s == pytest.approx(30.0)


def test_only_the_tail_window_counts():
    # 前 20 s 在趋稳（从 1300 爬到 1413），窗口只看最后 30 s
    values = [1300 + 113 * min(i / 20, 1) for i in range(60)]
    assert assess(series(values), CFG).verdict == "PASS"


def test_linear_drift_fails_and_is_reported_as_drift():
    values = [1000 * (1 + 0.002 * i) for i in range(40)]  # 30 s 内漂 6%
    result = assess(series(values), CFG)
    assert result.verdict == "FAIL"
    assert "drift" in result.reasons
    assert result.drift == pytest.approx(0.06, rel=0.1)
    assert result.representative_kappa25 is None


def test_slight_drift_warns():
    values = [1000 * (1 + 0.0003 * i) for i in range(40)]  # 30 s 内约 0.9%
    result = assess(series(values), CFG)
    assert result.verdict == "WARN"
    assert result.reasons == ("slight_drift",)
    assert result.representative_kappa25 is not None


def test_noise_levels():
    assert "variation" in assess(series(noisy(1000, 0.01)), CFG).reasons
    assert assess(series(noisy(1000, 0.05)), CFG).verdict == "FAIL"


def test_drift_threshold_is_relative_not_absolute():
    # 同样 0.3 µS/cm/s 的漂移：对 84 µS/cm 是 FAIL，对 12880 µS/cm 无所谓
    assert assess(series([84 + 0.3 * i for i in range(40)]), CFG).verdict == "FAIL"
    assert assess(series([12880 + 0.3 * i for i in range(40)]), CFG).verdict == "PASS"


def test_low_conductivity_uses_floor():
    # 纯水 1.5 µS/cm、噪声 0.02 µS/cm：相对量按 10 µS/cm 下限算，不至于判不稳
    values = [1.5 + 0.02 * ((-1) ** i) for i in range(40)]
    assert assess(series(values), CFG).verdict == "PASS"


def test_hard_flag_in_window_fails():
    flags = [()] * 40
    flags[35] = ("OPEN_CIRCUIT",)
    result = assess(series([1000.0] * 40, flags=flags), CFG)
    assert result.verdict == "FAIL"
    assert result.hard_flags == ("OPEN_CIRCUIT",)


def test_hard_flag_before_window_is_ignored():
    flags = [()] * 40
    flags[2] = ("OPEN_CIRCUIT",)
    assert assess(series([1000.0] * 40, flags=flags), CFG).verdict == "PASS"


def test_soft_flag_warns():
    flags = [()] * 40
    flags[-1] = ("WAVEFORM_UNSTABLE",)
    assert assess(series([1000.0] * 40, flags=flags), CFG).reasons == ("waveform_unstable",)


def test_invalid_frames():
    values = [1000.0] * 40
    values[-3] = None  # 温度探头掉了一帧
    result = assess(series(values), CFG)
    assert result.verdict == "WARN" and "some_invalid" in result.reasons
    values[-10:] = [None] * 10
    assert "too_many_invalid" in assess(series(values), CFG).reasons


def test_too_few_points_fail():
    result = assess(series([1000.0] * 5, dt=10.0), CFG)
    assert result.verdict == "FAIL"
    assert "insufficient_points" in result.reasons


def test_short_measurement_warns_window_incomplete():
    result = assess(series([1000.0] * 20), CFG)  # 只测了 19 s
    assert result.verdict == "WARN"
    assert result.reasons == ("window_incomplete",)


def test_temperature_change_warns():
    temps = [24.0 + 0.05 * i for i in range(40)]  # 窗口内升 1.5 °C
    result = assess(series([1000.0] * 40, temps=temps), CFG)
    assert "temperature_unstable" in result.reasons
    assert result.temperature_span == pytest.approx(1.5)


def test_empty():
    result = assess([], CFG)
    assert result.verdict == "FAIL" and result.reasons == ("no_data",)


def test_result_serialises_with_representative():
    doc = assess(series(noisy(500.0, 0.001)), CFG).as_dict()
    assert doc["verdict"] == "PASS"
    assert doc["representative_kappa25"] == doc["kappa25_mean"]
    assert isinstance(doc["reasons"], list)
