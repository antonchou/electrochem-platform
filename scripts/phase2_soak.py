"""Exercise the integrated Mock -> WebSocket -> SQLite loop for a fixed period."""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=1800.0, help="seconds")
    parser.add_argument("--points", type=int, help="stop after this many live frames")
    parser.add_argument("--sample-rate", type=float, default=10.0, help="Hz")
    parser.add_argument(
        "--scenario",
        choices=("stable", "noisy", "drift", "dropout"),
        default="stable",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        help="SQLite output; default is a timestamped file under data/raw",
    )
    return parser.parse_args()


def _execute_session(args: argparse.Namespace, db_path: Path) -> dict[str, object]:
    """Run TestClient HTTP and WebSocket operations in one worker thread."""
    from fastapi.testclient import TestClient

    from backend.app import storage
    from backend.app.main import app

    received = 0
    last_timestamp: float | None = None
    experiment_id: int | None = None

    with TestClient(app) as client:
        with client.websocket_connect("/ws/stream") as websocket:
            started = client.post(
                "/api/experiment/start",
                json={"sample_id": "PHASE2_SOAK", "sensor_path_id": "MOCK_EC_01"},
            )
            started.raise_for_status()
            body = started.json()
            if not body.get("ok"):
                raise RuntimeError(f"experiment did not start: {body}")
            experiment_id = int(body["experiment_id"])

            deadline = time.monotonic() + args.duration
            try:
                while args.points is None or received < args.points:
                    if time.monotonic() >= deadline:
                        break
                    item = websocket.receive_json()
                    if "ec" not in item:
                        continue

                    timestamp = float(item["timestamp"])
                    if last_timestamp is not None and timestamp < last_timestamp:
                        raise RuntimeError(
                            f"live timestamp moved backwards: {last_timestamp} -> {timestamp}"
                        )
                    last_timestamp = timestamp
                    float(item["ec"])
                    float(item["temperature"])
                    received += 1
            finally:
                client.post("/api/experiment/stop")

    if experiment_id is None:
        raise RuntimeError("experiment id was not created")
    frames = storage.get_frames(experiment_id, limit=1_000_000)
    minimum_expected = (
        args.points
        if args.points is not None
        else max(1, int(args.duration * args.sample_rate * 0.8))
    )
    if received < minimum_expected:
        raise RuntimeError(
            f"only received {received} frames; expected at least {minimum_expected}"
        )
    if len(frames) < minimum_expected:
        raise RuntimeError(
            f"only persisted {len(frames)} frames; expected at least {minimum_expected}"
        )

    sequence = [int(frame["seq_no"]) for frame in frames]
    expected_sequence = list(range(1, len(sequence) + 1))
    if sequence != expected_sequence:
        raise RuntimeError("persisted seq_no values are not contiguous")
    monotonic = [int(frame["monotonic_ms"]) for frame in frames]
    if any(right < left for left, right in zip(monotonic, monotonic[1:])):
        raise RuntimeError("persisted monotonic_ms moved backwards")
    if any("SIMULATED" not in (frame["quality_flags"] or "") for frame in frames):
        raise RuntimeError("one or more persisted frames lack the SIMULATED flag")

    return {
        "result": "pass",
        "experiment_id": experiment_id,
        "scenario": args.scenario,
        "sample_rate_hz": args.sample_rate,
        "received_frames": received,
        "persisted_frames": len(frames),
        "db_path": str(db_path),
    }


def run(args: argparse.Namespace) -> dict[str, object]:
    if args.duration <= 0:
        raise ValueError("duration must be positive")
    if args.points is not None and args.points <= 0:
        raise ValueError("points must be positive")
    if args.sample_rate <= 0:
        raise ValueError("sample-rate must be positive")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    db_path = args.db_path or REPO_ROOT / "data" / "raw" / f"phase2-soak-{stamp}.db"
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    os.environ["EC_DB_PATH"] = str(db_path)
    os.environ["EC_MOCK_SCENARIO"] = args.scenario
    os.environ["EC_SAMPLE_RATE_HZ"] = str(args.sample_rate)

    outcome: queue.Queue[dict[str, object] | BaseException] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            outcome.put(_execute_session(args, db_path))
        except BaseException as exc:  # propagate worker failures to the CLI thread
            outcome.put(exc)

    thread = threading.Thread(
        target=worker,
        name="phase2-soak-session",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=args.duration + 15.0)
    if thread.is_alive():
        raise RuntimeError("soak session exceeded duration by more than 15 seconds")

    result = outcome.get_nowait()
    if isinstance(result, BaseException):
        raise result
    return result


def main() -> None:
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
