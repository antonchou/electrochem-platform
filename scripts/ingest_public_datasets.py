#!/usr/bin/env python3
"""Convert public electrochemistry datasets into the project's 4-column ingest CSV.

Output columns (CsvPlaybackDriver):
    time_s, voltage_v, current, temperature_c

Sources (see data/fixtures/ingest/SOURCES.md):
  - Zenodo 6985321 Braun et al. Li-ion cell time/I/V/T
  - Zenodo 7244939 Rahmanian et al. EIS conductivity vs T (invert to U/I)
  - echemdb hermann_2021 CV (E/I + metadata temperature)
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "fixtures" / "ingest"
CACHE = Path("/tmp/ec-data/raw")

BRAUN_URL = "https://zenodo.org/records/6985321/files/Experimental_data_fresh_cell.csv?download=1"
RAHMANIAN_URL = "https://zenodo.org/records/7244939/files/Conductivtiy_experiment.csv?download=1"
ECHEMDB_CSV = (
    "https://raw.githubusercontent.com/echemdb/electrochemistry-data/main/"
    "literature/source_data/hermann_2021_effect_138279/"
    "hermann_2021_effect_138279_f5a_black.csv"
)


def _open(url: str):
    return urllib.request.urlopen(url, timeout=180)


def write_ingest(path: Path, rows: list[tuple[float, float, float, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["time_s", "voltage_v", "current", "temperature_c"])
        for row in rows:
            w.writerow(row)


def convert_braun(max_seconds: float = 600.0) -> Path:
    """Li-ion pouch/cell cycling: Time, Current, Voltage, Temperature."""
    cached = CACHE / "braun_fresh_head.csv"
    if cached.exists():
        text = cached.read_text(encoding="utf-8")
    else:
        lines = []
        with _open(BRAUN_URL) as resp:
            buf = b""
            n = 0
            while n < int(max_seconds) + 5:
                chunk = resp.read(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf and n < int(max_seconds) + 5:
                    line, buf = buf.split(b"\n", 1)
                    lines.append(line.decode("utf-8", "replace"))
                    n += 1
        text = "\n".join(lines)
    rows: list[tuple[float, float, float, float]] = []
    for rec in csv.DictReader(io.StringIO(text)):
        t = float(rec["Time"])
        if t > max_seconds:
            break
        rows.append(
            (
                round(t, 3),
                float(rec["Voltage"]),
                float(rec["Current"]),
                float(rec["Temperature"]),
            )
        )
    out = OUT / "braun_2022_lib_cell.csv"
    write_ingest(out, rows)
    return out


def convert_rahmanian(experiment_id: str = "PYA_25082021_BM169_1") -> Path:
    """EIS conductivity (S/cm) vs T → assumed 1 V excitation + published Kcell."""
    cached = CACHE / "rahmanian_bm169.json"
    records: list[dict]
    if cached.exists():
        records = json.loads(cached.read_text(encoding="utf-8"))
    else:
        records = []
        with _open(RAHMANIAN_URL) as resp:
            reader = csv.DictReader(io.TextIOWrapper(resp, encoding="utf-8"), delimiter=";")
            for rec in reader:
                if rec.get("experimentID") != experiment_id:
                    if records:
                        break
                    continue
                records.append(rec)
    rows: list[tuple[float, float, float, float]] = []
    kcell = 4.72026
    u_v = 1.0
    for i, rec in enumerate(records):
        t_c = float(rec["temperature"])
        kappa_s_cm = float(rec["EIS_conductivity"])
        g_s = kappa_s_cm / kcell
        i_a = g_s * u_v
        rows.append((float(i), u_v, i_a, t_c))
    out = OUT / "rahmanian_2022_eis_bm169.csv"
    write_ingest(out, rows)
    sidecar = {
        "experiment_id": experiment_id,
        "cell_constant_per_cm": kcell,
        "excitation_voltage_v": u_v,
        "note": "U/I reconstructed from EIS_conductivity and published Kcell so compute_chain can run; not a measured I–V waveform.",
    }
    (OUT / "rahmanian_2022_eis_bm169.meta.json").write_text(
        json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
    )
    return out


def convert_echemdb(stride: int = 10, temperature_c: float = 20.0) -> Path:
    """CV of Au(111) in HClO4/HCOOH: Time, WE potential, current; T from YAML (20 °C)."""
    cached = CACHE / "hermann_2021_effect_138279_f5a_black.csv"
    if cached.exists():
        text = cached.read_text(encoding="utf-8-sig")
    else:
        text = _open(ECHEMDB_CSV).read().decode("utf-8-sig")
    raw = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
    rows: list[tuple[float, float, float, float]] = []
    t0 = float(raw[0]["Time (s)"])
    for rec in raw[::stride]:
        rows.append(
            (
                round(float(rec["Time (s)"]) - t0, 4),
                float(rec["WE(1).Potential (V)"]),
                float(rec["WE(1).Current (A)"]),
                temperature_c,
            )
        )
    out = OUT / "echemdb_hermann_2021_cv.csv"
    write_ingest(out, rows)
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    braun = convert_braun()
    rahmanian = convert_rahmanian()
    echemdb = convert_echemdb()
    print("wrote", braun, "rows", sum(1 for _ in braun.open()) - 1)
    print("wrote", rahmanian, "rows", sum(1 for _ in rahmanian.open()) - 1)
    print("wrote", echemdb, "rows", sum(1 for _ in echemdb.open()) - 1)


if __name__ == "__main__":
    main()
