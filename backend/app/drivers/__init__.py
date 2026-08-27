"""Device-driver contracts and built-in adapters."""

from .base import DeviceDriver, DriverReading
from .csv_playback import CsvPlaybackConfig, CsvPlaybackDriver
from .mock import MockDevice, MockDeviceConfig, MockScenario, load_mock_config
from .simulator import (
    FaultKind,
    SimulatorConfig,
    SimulatorDriver,
    SimulatorMode,
    load_simulator_config,
)

__all__ = [
    "DeviceDriver",
    "DriverReading",
    "MockDevice",
    "MockDeviceConfig",
    "MockScenario",
    "load_mock_config",
    "CsvPlaybackConfig",
    "CsvPlaybackDriver",
    "SimulatorConfig",
    "SimulatorDriver",
    "SimulatorMode",
    "FaultKind",
    "load_simulator_config",
]
