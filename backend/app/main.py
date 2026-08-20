import asyncio
import math
import time
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from fastapi.responses import RedirectResponse

app = FastAPI(
    title="Raspberry Pi Electrochem Platform",
    version="0.1.0",
)

STARTED_MONOTONIC = time.monotonic()

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs", status_code=307)

@app.get("/")
async def root() -> dict:
    return {
        "service": "electrochem-backend",
        "status": "running",
        "health": "/health",
        "docs": "/docs",
        "mock_websocket": "/ws/mock",
    }

@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "electrochem-backend",
        "phase": "0.3",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.websocket("/ws/mock")
async def mock_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    seq = 0

    try:
        while True:
            elapsed = time.monotonic() - STARTED_MONOTONIC
            ec_value = 500.0 + 20.0 * math.sin(elapsed / 8.0)
            temperature = 25.0 + 0.3 * math.sin(elapsed / 15.0)

            await websocket.send_json(
                {
                    "schema_version": "0.1.0",
                    "seq": seq,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "monotonic_ms": round(elapsed * 1000),
                    "sensor_path_id": "MOCK_EC_01",
                    "calibration_id": None,
                    "quality_flags": [],
                    "raw": {
                        "ec_us_cm": round(ec_value, 3),
                        "temperature_c": round(temperature, 3),
                    },
                    "calibrated": None,
                    "derived": None,
                    "status": "streaming",
                }
            )

            seq += 1
            await asyncio.sleep(1)

    except WebSocketDisconnect:
        pass
