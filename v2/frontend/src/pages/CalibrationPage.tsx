import { useEffect, useState, type FormEvent } from 'react';
import { api, errorText } from '../lib/api.ts';
import { dateTime, fixed, kappaText } from '../lib/format.ts';
import type { Calibration, LabState, Measurement, Standard } from '../lib/types.ts';

interface Row {
  measurementId: string;
  standard: string; // 预设名称，或 'custom'
  customName: string;
  customValue: string;
}

const emptyRow = (): Row => ({ measurementId: '', standard: 'KCl 0.01 mol/L', customName: '', customValue: '' });

export function CalibrationPage({ lab }: { lab: LabState | null }) {
  const [calibrations, setCalibrations] = useState<Calibration[]>([]);
  const [measurements, setMeasurements] = useState<Measurement[]>([]);
  const [standards, setStandards] = useState<Standard[]>([]);
  const [rows, setRows] = useState<Row[]>([emptyRow()]);
  const [operator, setOperator] = useState('');
  const [lot, setLot] = useState('');
  const [cellId, setCellId] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let alive = true;
    Promise.all([api.calibrations(), api.measurements(), api.standards()])
      .then(([c, m, s]) => {
        if (!alive) return;
        setCalibrations(c);
        setMeasurements(m);
        setStandards(s);
      })
      .catch((e) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [version, lab?.last_finished?.id]);

  // 只有正常结束、判稳有代表值的测量能当标准液点
  const usable = measurements.filter((m) => m.status === 'completed' && m.qc?.representative_kappa25 != null);
  const running = !!lab?.measurement;

  const update = (index: number, patch: Partial<Row>) =>
    setRows((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const buildPoints = () =>
    rows.map((row, i) => {
      const measurementId = Number(row.measurementId);
      if (!row.measurementId) throw new Error(`第 ${i + 1} 行没选测量`);
      if (row.standard === 'custom') {
        const value = Number(row.customValue);
        if (!row.customName.trim() || !Number.isFinite(value) || value <= 0) {
          throw new Error(`第 ${i + 1} 行：自定义标准液要填名称和 > 0 的 κ25`);
        }
        return { measurement_id: measurementId, standard_name: row.customName.trim(), standard_kappa25_us_cm: value };
      }
      const preset = standards.find((s) => s.name === row.standard);
      if (!preset) throw new Error(`第 ${i + 1} 行：未知标准液`);
      return { measurement_id: measurementId, standard_name: preset.name, standard_kappa25_us_cm: preset.kappa25_us_cm };
    });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.calibrate({
        points: buildPoints(),
        operator: operator.trim() || null,
        lot: lot.trim() || null,
        cell_id: cellId.trim() || null,
        note: note.trim() || null,
      });
      setRows([emptyRow()]);
      setVersion((v) => v + 1);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const current = lab?.calibration ?? null;

  return (
    <div className="calibration">
      <section className="card" data-testid="current-calibration">
        <h2>当前电池常数</h2>
        {current ? (
          <CalibrationCard calibration={current} />
        ) : (
          <p>
            未标定：使用标称 Kcell = {fixed(lab?.cell_constant_per_cm, 4)} cm⁻¹。此时的 κ
            只能在同一导电池的测量之间作相对比较；要得到可比的绝对值，请用标准液标定。
          </p>
        )}
      </section>

      <section className="card">
        <h2>新建标定</h2>
        <p className="hint">
          步骤：把电极放进标准液 → 在「测量」页测到稳定并停止 → 在这里选中那次测量和对应的标准液。多种标准液（如 147 与 1413
          µS/cm）一起标定时按过原点最小二乘求 Kcell，并给出各点偏差。新标定只影响之后开始的测量；已有测量保留当时的 Kcell。
        </p>
        <form onSubmit={submit} className="calibration-form">
          {rows.map((row, i) => (
            <div className="calibration-row" key={i}>
              <select
                value={row.measurementId}
                onChange={(e) => update(i, { measurementId: e.target.value })}
                data-testid={`cal-measurement-${i}`}
              >
                <option value="">选择标准液的测量…</option>
                {usable.map((m) => (
                  <option key={m.id} value={m.id}>
                    #{m.id} {m.sample_name} · {kappaText(m.qc?.representative_kappa25)} · {m.qc?.verdict}
                  </option>
                ))}
              </select>
              <select
                value={row.standard}
                onChange={(e) => update(i, { standard: e.target.value })}
                data-testid={`cal-standard-${i}`}
              >
                {standards.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.name}（{kappaText(s.kappa25_us_cm)}）
                  </option>
                ))}
                <option value="custom">自定义…</option>
              </select>
              {row.standard === 'custom' && (
                <>
                  <input
                    placeholder="标准液名称"
                    value={row.customName}
                    onChange={(e) => update(i, { customName: e.target.value })}
                  />
                  <input
                    placeholder="25 °C 标称 κ（µS/cm）"
                    inputMode="decimal"
                    value={row.customValue}
                    onChange={(e) => update(i, { customValue: e.target.value })}
                  />
                </>
              )}
              {rows.length > 1 && (
                <button type="button" onClick={() => setRows((prev) => prev.filter((_, j) => j !== i))}>
                  删除
                </button>
              )}
            </div>
          ))}
          <div className="toolbar">
            <button type="button" onClick={() => setRows((prev) => [...prev, emptyRow()])}>
              再加一种标准液
            </button>
          </div>
          <div className="meta-fields">
            <input placeholder="操作者" value={operator} onChange={(e) => setOperator(e.target.value)} maxLength={100} />
            <input placeholder="标准液批次" value={lot} onChange={(e) => setLot(e.target.value)} maxLength={100} />
            <input placeholder="导电池编号" value={cellId} onChange={(e) => setCellId(e.target.value)} maxLength={100} />
            <input placeholder="备注" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
          </div>
          <button type="submit" className="primary" disabled={busy || running || usable.length === 0} data-testid="calibrate">
            计算并启用新标定
          </button>
          {running && <p className="hint">测量进行中，停止后才能更换标定。</p>}
          {usable.length === 0 && <p className="hint">还没有可用的测量：先测一次标准液并等它判稳。</p>}
          {error && (
            <p className="error" data-testid="calibration-error">
              {error}
            </p>
          )}
        </form>
      </section>

      {calibrations.length > 0 && (
        <section className="card">
          <h2>标定历史</h2>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>时间</th>
                <th>Kcell (cm⁻¹)</th>
                <th>各点 RSD</th>
                <th>标准液</th>
                <th>操作者 / 批次</th>
              </tr>
            </thead>
            <tbody>
              {calibrations.map((c) => (
                <tr key={c.id}>
                  <td>{c.id}</td>
                  <td>{dateTime(c.created_at)}</td>
                  <td className="num">{fixed(c.cell_constant_per_cm, 4)}</td>
                  <td className="num">{c.rsd_pct === null ? '—' : `${fixed(c.rsd_pct, 2)}%`}</td>
                  <td>{c.points.map((p) => p.standard_name).join('、')}</td>
                  <td>
                    {c.operator ?? '—'} / {c.lot ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

function CalibrationCard({ calibration }: { calibration: Calibration }) {
  return (
    <div>
      <p className="kcell">
        Kcell = <strong data-testid="kcell-value">{fixed(calibration.cell_constant_per_cm, 4)}</strong> cm⁻¹
        <span className="hint">
          {' '}
          标定 #{calibration.id} · {dateTime(calibration.created_at)}
          {calibration.rsd_pct !== null ? ` · 各点 Kcell 相对标准差 ${fixed(calibration.rsd_pct, 2)}%` : ''}
          {calibration.operator ? ` · ${calibration.operator}` : ''}
          {calibration.lot ? ` · 批次 ${calibration.lot}` : ''}
        </span>
      </p>
      <table>
        <thead>
          <tr>
            <th>标准液</th>
            <th>标称 κ25</th>
            <th>所用测量</th>
            <th>测得 G25</th>
            <th>标定后偏差</th>
          </tr>
        </thead>
        <tbody>
          {calibration.points.map((p) => (
            <tr key={p.measurement_id}>
              <td>{p.standard_name}</td>
              <td className="num">{kappaText(p.standard_kappa25_us_cm)}</td>
              <td>
                #{p.measurement_id} {p.sample_name}
              </td>
              <td className="num">{(p.conductance25_s * 1e3).toFixed(5)} mS</td>
              <td className="num">{p.deviation_pct === null ? '—' : `${p.deviation_pct.toFixed(2)}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
