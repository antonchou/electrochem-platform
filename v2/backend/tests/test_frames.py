import pytest

from ec.frames import (
    DEVICE_RESTART,
    SEQ_GAP,
    FrameError,
    SequenceCheck,
    join_flags,
    parse_frame,
    parse_line,
    split_flags,
)

# 固件 README 里的示例帧，原样
FIRMWARE_LINE = (
    '{"schema_version":2,"seq_no":1,"monotonic_ms":0,"voltage_raw_v":0.0999999,'
    '"current_raw_a":9.99999e-05,"temperature_raw_c":25.0625,"quality_flags":null,'
    '"device_id":"ESP32-IV-3C71BF","firmware_version":"0.1.0-mpy","range_id":"RS1000R_G1",'
    '"excitation_frequency_hz":10.0,"excitation_amplitude_v":0.2}'
)


def test_parses_the_firmware_frame():
    kind, r = parse_line(FIRMWARE_LINE + "\r\n")
    assert kind == "frame"
    assert (r.voltage_v, r.current_a, r.temperature_c) == (0.0999999, 9.99999e-05, 25.0625)
    assert r.flags == ()
    assert (r.device_seq, r.device_ms) == (1, 0)
    assert r.info.device_id == "ESP32-IV-3C71BF"
    assert r.info.range_id == "RS1000R_G1"
    assert r.info.excitation_amplitude_v == 0.2


def test_firmware_flags_and_nulls():
    r = parse_frame({
        "seq_no": 5, "voltage_raw_v": None, "current_raw_a": None, "temperature_raw_c": None,
        "quality_flags": "DROPOUT|TEMP_INVALID",
    })
    assert (r.voltage_v, r.current_a, r.temperature_c) == (None, None, None)
    assert r.flags == ("DROPOUT", "TEMP_INVALID")


def test_wrongly_typed_values_become_missing_not_errors():
    r = parse_frame({"seq_no": True, "voltage_raw_v": "0.1", "current_raw_a": False, "temperature_raw_c": 1e999})
    assert r.device_seq is None
    assert (r.voltage_v, r.current_a, r.temperature_c) == (None, None, None)


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("# INFO ADS1256 正常：STATUS=0x30", "log"),
        ("   ", "empty"),
        ("ets Jun  8 2016 00:22:57", "noise"),  # ESP32 上电杂讯
        ('{"seq_no": 1, "voltage_raw_v": 0.1', "noise"),  # 断行
        ('{"hello": 1}', "noise"),  # 合法 JSON 但不是帧
    ],
)
def test_non_frame_lines(line, kind):
    assert parse_line(line)[0] == kind


def test_log_text_is_stripped_of_marker():
    assert parse_line("# WARN DS18B20 未就绪") == ("log", "WARN DS18B20 未就绪")


def test_parse_frame_rejects_non_frames():
    with pytest.raises(FrameError):
        parse_frame([1, 2])
    with pytest.raises(FrameError):
        parse_frame({"seq_no": 1})


def test_flags_round_trip():
    assert split_flags("A| B||A") == ("A", "B")
    assert split_flags(["X", "Y"]) == ("X", "Y")
    assert join_flags(()) is None
    assert join_flags(("A", "B")) == "A|B"


def test_sequence_check_detects_gaps_and_restarts():
    check = SequenceCheck()
    assert check.check(1) == ()
    assert check.check(2) == ()
    assert check.check(5) == (SEQ_GAP,)
    assert check.check(1) == (DEVICE_RESTART,)
    assert check.check(None) == ()
    assert check.check(2) == ()
