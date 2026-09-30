import { useEffect, useMemo, useState } from 'react';
import type uPlot from 'uplot';
import { Chart, kappaTimeData, kappaTimeOptions, rawTimeData, rawTimeOptions, type ChartOptions } from '../components/Chart.tsx';
import { QcSummary, VerdictBadge } from '../components/Qc.tsx';
import { api, errorText } from '../lib/api.ts';
import { dateTime, duration, fixed, kappaText, sig, STATUS_LABELS } from '../lib/format.ts';
import type { ConcentrationAnalysis, Measurement, Param, Point, TemperatureFit } from '../lib/types.ts';

interface Props {
  refreshKey: string; // 测量开始或结束时变化，触发刷新列表
  focusId: number | null;
}

export function RecordsPage({ refreshKey, focusId }: Props) {
  const [rows, setRows] = useState<Measurement[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<number | null>(focusId);
  const [selected, setSelected] = useState<Set<number>>(new Set());

  useEffect(() => {
    let alive = true;
    api
      .measurements()
      .then((r) => alive && setRows(r))
      .catch((e) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const chosen = rows.filter((row) => selected.has(row.id));

  return (
    <div className="records">
      <section className="card">
        <h2>测量记录</h2>
        {error && <p className="error">{error}</p>}
        {rows.length === 0 ? (
          <p className="hint">还没有测量。到「测量」页开始第一次测量。</p>
        ) : (
          <div className="table-wrap">
            <table data-testid="records-table">
              <thead>
                <tr>
                  <th title="勾选后在下方比较">比较</th>
                  <th>#</th>
                  <th>样品</th>
                  <th>浓度 mmol/L</th>
                  <th>开始</th>
                  <th>时长</th>
                  <th>代表 κ25</th>
                  <th>判稳</th>
                  <th>Kcell</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.id}
                    className={row.id === detailId ? 'selected' : undefined}
                    onClick={() => setDetailId(row.id)}
                    data-testid={`record-${row.id}`}
                  >
                    <td onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={selected.has(row.id)}
                        onChange={() => toggle(row.id)}
                        aria-label={`选择测量 ${row.id}`}
                      />
                    </td>
                    <td>{row.id}</td>
                    <td>{row.sample_name}</td>
                    <td>{row.concentration_mmol_l ?? '—'}</td>
                    <td>{dateTime(row.started_at)}</td>
                    <td>{duration(row.duration_s)}</td>
                    <td className="num">{kappaText(row.qc?.representative_kappa25)}</td>
                    <td>
                      <VerdictBadge verdict={row.qc?.verdict} testId={`verdict-${row.id}`} />
                    </td>
                    <td className="num" title={row.calibration_id ? `标定 #${row.calibration_id}` : '未标定'}>
                      {fixed(row.cell_constant_per_cm, 4)}
                      {row.calibration_id ? '' : '*'}
                    </td>
                    <td>{STATUS_LABELS[row.status] ?? row.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="hint">* 未标定：Kcell 为标称值，κ 只能作相对比较。点击一行看详情，勾选多行做比较。</p>
          </div>
        )}
      </section>

      {chosen.length >= 2 && <Compare rows={chosen} />}
      {detailId !== null && <Detail key={detailId} id={detailId} />}
    </div>
  );
}

function Detail({ id }: { id: number }) {
  const [measurement, setMeasurement] = useState<Measurement | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [total, setTotal] = useState(0);
  const [raw, setRaw] = useState(false);
  const [alphaFit, setAlphaFit] = useState<TemperatureFit | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([api.measurement(id), api.points(id)])
      .then(([m, p]) => {
        if (!alive) return;
        setMeasurement(m);
        setPoints(p.points);
        setTotal(p.total);
      })
      .catch((e) => alive && setError(errorText(e)));
    return () => {
      alive = false;
    };
  }, [id]);

  const kappaOptions = useMemo(() => kappaTimeOptions(), []);
  const rawOptions = useMemo(() => rawTimeOptions(), []);
  const data = useMemo(() => (raw ? rawTimeData(points) : kappaTimeData(points)), [raw, points]);

  if (error) return <section className="card error">{error}</section>;
  if (!measurement) return <section className="card">加载中……</section>;
  const m = measurement;

  return (
    <section className="card detail" data-testid="record-detail">
      <h2>
        测量 #{m.id} · {m.sample_name} <VerdictBadge verdict={m.qc?.verdict} testId="detail-verdict" />
      </h2>
      <div className="detail-grid">
        <dl className="facts">
          <dt>浓度</dt>
          <dd>{m.concentration_mmol_l === null ? '未填' : `${m.concentration_mmol_l} mmol/L`}</dd>
          <dt>时间</dt>
          <dd>
            {dateTime(m.started_at)} → {dateTime(m.ended_at)}（{STATUS_LABELS[m.status]}）
          </dd>
          <dt>帧数</dt>
          <dd>{m.frame_count ?? total}</dd>
          <dt>Kcell</dt>
          <dd>
            {fixed(m.cell_constant_per_cm, 4)} cm⁻¹{m.calibration_id ? `（标定 #${m.calibration_id}）` : '（未标定，标称值）'}
          </dd>
          <dt>温补</dt>
          <dd>线性，α = {fixed(m.alpha_per_c, 4)} /°C</dd>
          <dt>设备</dt>
          <dd>
            {m.device_id ?? m.device_kind} · {m.firmware_version ?? '—'} · 量程 {m.range_id ?? '—'}
          </dd>
          <dt>激励</dt>
          <dd>
            {fixed(m.excitation_frequency_hz, 1)} Hz · {fixed(m.excitation_amplitude_v, 3)} V
          </dd>
          {m.note && (
            <>
              <dt>备注</dt>
              <dd>{m.note}</dd>
            </>
          )}
        </dl>
        {m.qc ? <QcSummary qc={m.qc} /> : <p className="hint">没有判稳结果（测量被中断）。</p>}
      </div>
      <div className="toolbar">
        <label>
          <input type="checkbox" checked={raw} onChange={(e) => setRaw(e.target.checked)} /> 看原始 U / I
        </label>
        <a className="button" href={api.csvUrl(m.id)} download data-testid="export-csv">
          导出 CSV
        </a>
        <button
          type="button"
          onClick={() =>
            api
              .temperatureFit(m.id)
              .then(setAlphaFit)
              .catch((e) => setError(errorText(e)))
          }
        >
          由 κ(T)–T 估计 α
        </button>
        {total > points.length && <span className="hint">曲线按 {points.length} / {total} 点等间隔抽样显示</span>}
      </div>
      <Chart options={raw ? rawOptions : kappaOptions} data={data} height={280} testId="detail-chart" />
      {alphaFit && <AlphaFit fit={alphaFit} />}
    </section>
  );
}

function AlphaFit({ fit }: { fit: TemperatureFit }) {
  if (!fit.ok) return <p className="hint">无法估计 α：{fit.error}</p>;
  return (
    <p className="fit-line" data-testid="alpha-fit">
      α = {param(fit.alpha_per_c, 5)} /°C，κ25 = {param(fit.kappa25_us_cm, 1)} µS/cm，R² = {fixed(fit.r2, 5)}，n = {fit.n}，温度{' '}
      {fixed(fit.temperature_range_c[0], 2)}–{fixed(fit.temperature_range_c[1], 2)} °C
    </p>
  );
}

function param(p: Param, digits: number): string {
  const ci = p.ci95 ? `（95% CI ${p.ci95[0].toFixed(digits)} ~ ${p.ci95[1].toFixed(digits)}）` : '';
  return `${p.value.toFixed(digits)}${ci}`;
}

function Compare({ rows }: { rows: Measurement[] }) {
  const [analysis, setAnalysis] = useState<ConcentrationAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const withValue = rows
    .filter((r) => r.qc?.representative_kappa25 != null)
    .sort((a, b) => (a.qc!.representative_kappa25 as number) - (b.qc!.representative_kappa25 as number));
  const without = rows.filter((r) => r.qc?.representative_kappa25 == null);
  const max = Math.max(...withValue.map((r) => r.qc!.representative_kappa25 as number), 0);
  const fittable = withValue.filter((r) => r.concentration_mmol_l !== null);
  const mixed = mixedCellConstants(withValue);
  const idsKey = rows.map((r) => r.id).join(',');

  useEffect(() => {
    setAnalysis(null);
    setError(null);
  }, [idsKey]);

  const runFit = () => {
    setError(null);
    api
      .concentration(fittable.map((r) => r.id))
      .then(setAnalysis)
      .catch((e) => setError(errorText(e)));
  };

  return (
    <section className="card compare" data-testid="compare">
      <h2>比较（{rows.length} 次测量）</h2>
      <div className="bars">
        {withValue.map((r) => {
          const value = r.qc!.representative_kappa25 as number;
          return (
            <div className="bar-row" key={r.id}>
              <span className="bar-label">
                #{r.id} {r.sample_name}
              </span>
              <span className="bar-track">
                <span className="bar" style={{ width: `${max > 0 ? (value / max) * 100 : 0}%` }} />
              </span>
              <span className="bar-value">{kappaText(value)}</span>
            </div>
          );
        })}
      </div>
      {mixed && (
        <p className="error" data-testid="mixed-kcell">
          所选测量用了不同的电池常数，κ25 之间有系统偏差：{mixed}。建议在同一次标定下测量。
        </p>
      )}
      {without.length > 0 && (
        <p className="hint">没有代表值、未参与比较：{without.map((r) => `#${r.id}`).join('、')}（判稳未通过或被中断）</p>
      )}
      <div className="toolbar">
        <button type="button" onClick={runFit} disabled={fittable.length < 2} data-testid="fit-concentration">
          浓度拟合（{fittable.length} 个有浓度的点）
        </button>
        {error && <span className="error">{error}</span>}
      </div>
      {analysis && <ConcentrationResult analysis={analysis} />}
    </section>
  );
}

/** 按所用标定分组；只有一组时返回 null（同后端 records.mixed_calibration_warnings）。 */
function mixedCellConstants(rows: Measurement[]): string | null {
  const groups = new Map<string, number[]>();
  for (const r of rows) {
    const key = `${r.calibration_id ? `标定 #${r.calibration_id}` : '未标定'}（Kcell ${fixed(r.cell_constant_per_cm, 4)}）`;
    groups.set(key, [...(groups.get(key) ?? []), r.id]);
  }
  if (groups.size <= 1) return null;
  return [...groups].map(([key, ids]) => `${key}：${ids.map((id) => `#${id}`).join('、')}`).join('；');
}

function ConcentrationResult({ analysis }: { analysis: ConcentrationAnalysis }) {
  const { options, data } = useMemo(() => concentrationChart(analysis), [analysis]);
  const linear = analysis.linear;
  const kohl = analysis.kohlrausch;
  return (
    <div className="concentration" data-testid="concentration-result">
      <table className="fit-table">
        <thead>
          <tr>
            <th>模型</th>
            <th>参数（95% 置信区间）</th>
            <th>R²</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>线性 κ25 = a + b·c</td>
            <td>{linear.ok ? `a = ${param(linear.intercept_us_cm, 2)} µS/cm；b = ${param(linear.slope_us_cm_per_mmol_l, 3)} µS/cm per mmol/L` : linear.error}</td>
            <td>{linear.ok ? fixed(linear.r2, 5) : '—'}</td>
          </tr>
          <tr>
            <td>Kohlrausch Λm = Λ0 − K·√c</td>
            <td>
              {kohl.ok
                ? `Λ0 = ${param(kohl.lambda0_s_cm2_per_mol, 2)} S·cm²/mol；K = ${param(kohl.k_s_cm2_per_mol_sqrt_l_per_mol, 2)}`
                : kohl.error}
            </td>
            <td>{kohl.ok ? fixed(kohl.r2, 5) : '—'}</td>
          </tr>
        </tbody>
      </table>
      <p className="hint">
        有效浓度区间 {analysis.range_mmol_l ? `${sig(analysis.range_mmol_l[0], 3)} ~ ${sig(analysis.range_mmol_l[1], 3)}` : '—'}{' '}
        mmol/L，区间外不外推。Kohlrausch 定律只适用于强电解质稀溶液。
      </p>
      <Chart options={options} data={data} height={280} testId="concentration-chart" />
    </div>
  );
}

function concentrationChart(analysis: ConcentrationAnalysis): { options: ChartOptions; data: uPlot.AlignedData } {
  const points = analysis.points;
  const cmin = Math.min(...points.map((p) => p.concentration_mmol_l));
  const cmax = Math.max(...points.map((p) => p.concentration_mmol_l));
  // 拟合曲线只画在数据覆盖的浓度区间内：区间外不外推
  const grid = Array.from({ length: 60 }, (_, i) => cmin + ((cmax - cmin) * i) / 59);
  const xs = [...new Set([...grid, ...points.map((p) => p.concentration_mmol_l)])].sort((a, b) => a - b);
  const measured = new Map(points.map((p) => [p.concentration_mmol_l, p.kappa25_us_cm]));
  const linear = analysis.linear;
  const kohl = analysis.kohlrausch;
  const data: uPlot.AlignedData = [
    xs,
    xs.map((x) => measured.get(x) ?? null),
    xs.map((x) => (linear.ok ? linear.intercept_us_cm.value + linear.slope_us_cm_per_mmol_l.value * x : null)),
    xs.map((x) =>
      kohl.ok && x > 0
        ? x * (kohl.lambda0_s_cm2_per_mol.value - kohl.k_s_cm2_per_mol_sqrt_l_per_mol.value * Math.sqrt(x / 1000))
        : null,
    ),
  ];
  const options: ChartOptions = {
    scales: { x: { time: false, range: (_u, _min, max) => [0, max] } },
    series: [
      { label: '浓度 (mmol/L)' },
      { label: '测量', stroke: '#2563eb', paths: () => null, points: { show: true, size: 9, fill: '#2563eb' } },
      { label: '线性', stroke: '#64748b', dash: [6, 4], width: 1.5, points: { show: false } },
      { label: 'Kohlrausch', stroke: '#d97706', width: 1.5, points: { show: false } },
    ],
    axes: [{ label: '浓度 (mmol/L)' }, { label: 'κ25 (µS/cm)', size: 70 }],
  };
  return { options, data };
}
