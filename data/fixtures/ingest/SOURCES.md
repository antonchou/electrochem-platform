# Public ingest fixtures

Converted to `time_s,voltage_v,current,temperature_c` for `CsvPlaybackDriver`.
Regenerate: `python3 scripts/ingest_public_datasets.py`

These are **not** conductivity-cell bench waveforms unless noted. Each file’s physical meaning is recorded below so κ25 is not mistaken for a literature conductivity.

| File | Source | What was measured | How we mapped it | License |
|---|---|---|---|---|
| `braun_2022_lib_cell.csv` | [Zenodo 6985321](https://zenodo.org/records/6985321) Braun et al., first 600 s of `Experimental_data_fresh_cell.csv` | Li-ion **cell** Time / Current / Voltage / Temperature (1 s) | columns as-is; U is terminal voltage (~3 V, always > 0) | see record |
| `rahmanian_2022_eis_bm169.csv` | [Zenodo 7244939](https://zenodo.org/records/7244939) Rahmanian et al., experiment `PYA_25082021_BM169_1` | EIS **conductivity** (S/cm) vs T (−20…60 °C), published Kcell = 4.72026 cm⁻¹ | **reconstructed** U = 1 V, I = (κ / Kcell)·U so `compute_chain` can run; not a measured I–V | CC (open dataset) |
| `echemdb_hermann_2021_cv.csv` | [echemdb hermann_2021](https://github.com/echemdb/electrochemistry-data) Fig. 5a black, every 10th point | Au(111) **CV** WE potential + current; YAML T = 20 °C | E → `voltage_v` (often ≤ 0 → `COMPUTE_INVALID`); I → `current`; T constant 20 | CC-BY-4.0 / ODC-By-1.0 |

Replay:

```bash
cd backend
EC_DRIVER=csv EC_CSV_PATH=../data/fixtures/ingest/braun_2022_lib_cell.csv \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For the EIS reconstruction also set `EC_CELL_CONSTANT=4.72026`.
