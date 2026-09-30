from pathlib import Path

import pytest

from ec.settings import Settings, SettingsError


def test_defaults_without_environment():
    s = Settings.from_env({})
    assert s.device == "sim"
    assert s.nominal_cell_constant_per_cm == 1.0
    assert s.qc.window_s == 30.0
    assert s.db_path.name == "ec.db"


def test_reads_environment():
    s = Settings.from_env({
        "EC_DEVICE": "replay", "EC_REPLAY_PATH": "run.jsonl", "EC_REPLAY_SPEED": "5", "EC_REPLAY_LOOP": "yes",
        "EC_CELL_CONSTANT": "0.98", "EC_QC_WINDOW_S": "10", "EC_PORT": "8010",
    })
    assert (s.device, s.replay_path, s.replay_speed, s.replay_loop) == ("replay", Path("run.jsonl"), 5.0, True)
    assert s.nominal_cell_constant_per_cm == 0.98
    assert s.qc.window_s == 10.0
    assert s.port == 8010


@pytest.mark.parametrize(
    ("env", "name"),
    [
        ({"EC_DEVICE": "ads1256"}, "EC_DEVICE"),
        ({"EC_DEVICE": "replay"}, "EC_REPLAY_PATH"),
        ({"EC_CELL_CONSTANT": "nan"}, "EC_CELL_CONSTANT"),
        ({"EC_CELL_CONSTANT": "0"}, "EC_CELL_CONSTANT"),
        ({"EC_SIM_RATE_HZ": "fast"}, "EC_SIM_RATE_HZ"),
        ({"EC_PORT": "0"}, "EC_PORT"),
        ({"EC_REPLAY_LOOP": "maybe"}, "EC_REPLAY_LOOP"),
        ({"EC_ALPHA": "inf"}, "EC_ALPHA"),
    ],
)
def test_invalid_values_name_the_variable(env, name):
    with pytest.raises(SettingsError, match=name):
        Settings.from_env(env)
