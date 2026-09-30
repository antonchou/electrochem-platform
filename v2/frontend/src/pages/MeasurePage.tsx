import { useMemo, useState, type FormEvent } from 'react';
import { Chart, kappaTimeData, kappaTimeOptions } from '../components/Chart.tsx';
import { Flags, QcSummary, VerdictBadge } from '../components/Qc.tsx';
import { api, errorText } from '../lib/api.ts';
import { duration, fixed, kappa, kappaText, reasonLabel, scaled } from '../lib/format.ts';
import type { LiveState } from '../lib/live.ts';

interface Props {
  live: LiveState;
  onShowRecord: (id: number) => void;
}

/** 浓度输入：空 = 未知；否则须为 ≥ 0 的数。返回 [值, 错误]。 */
function parseConcentration(text: string): [number | null, string | null] {
  const trimmed = text.trim();
  if (!trimmed) return [null, null];
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < 0) return [null, '浓度须为 ≥ 0 的数字，或留空'];
  return [value, null];
}

export function MeasurePage({ live, onShowRecord }: Props) {
  const lab = live.lab;
  const running = lab?.measurement ?? null;
  const [sampleName, setSampleName] = useState('');
  const [concentration, setConcentration] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const chartOptions = useMemo(() => kappaTimeOptions(), []);
  const chartData = useMemo(() => kappaTimeData(live.points), [live.points]);

  const [concentrationValue, concentrationError] = parseConcentration(concentration);
  const canStart = !!lab?.device.connected && !running && !busy && sampleName.trim() !== '' && !concentrationError;

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const start = (event: FormEvent) => {
    event.preventDefault();
    if (!canStart) return;
    void act(() =>
      api.start({ sample_name: sampleName.trim(), concentration_mmol_l: concentrationValue, note: note.trim() || null }),
    );
  };

  const latest = live.latest;
  const big = kappa(latest?.kappa25_us_cm);
  const lastFinished = lab?.last_finished ?? null;
  const qc = live.qc;

  return (
    <div className="measure">
      <section className="card form-card">
        <form onSubmit={start} className="sample-form">
          <label>
            样品名称
            <input
              value={running ? running.sample_name : sampleName}
              onChange={(e) => setSampleName(e.target.value)}
              disabled={!!running}
              placeholder="如 KCl 10 mM、自来水"
              maxLength={100}
              data-testid="sample-name"
            />
          </label>
          <label>
            浓度（mmol/L，可空）
            <input
              value={running ? (running.concentration_mmol_l ?? '').toString() : concentration}
              onChange={(e) => setConcentration(e.target.value)}
              disabled={!!running}
              inputMode="decimal"
              data-testid="concentration"
            />
          </label>
          <label className="wide">
            备注
            <input
              value={running ? running.note ?? '' : note}
              onChange={(e) => setNote(e.target.value)}
              disabled={!!running}
              maxLength={500}
            />
          </label>
          <div className="actions">
            {running ? (
              <button type="button" className="danger" disabled={busy} onClick={() => void act(api.stop)} data-testid="stop">
                停止测量
              </button>
            ) : (
              <button type="submit" className="primary" disabled={!canStart} data-testid="start">
                开始测量
              </button>
            )}
            <span className="mode" data-testid="mode">
              {running ? `测量 #${running.id} 记录中` : '监视中（不记录）'}
            </span>
          </div>
          {concentrationError && !running && <p className="error">{concentrationError}</p>}
          {error && (
            <p className="error" data-testid="action-error">
              {error}
            </p>
          )}
          {!lab?.device.connected && lab && <p className="hint">设备未连接：{lab.device.message ?? '等待设备'}</p>}
        </form>
      </section>

      <section className="card readout-card">
        <div className="big-reading">
          <span className="label">κ25</span>
          <span className="value" data-testid="kappa25">
            {big.text}
          </span>
          <span className="unit">{big.unit}</span>
        </div>
        <div className="small-readings">
          <Reading label="κ(T)" text={kappaText(latest?.kappa_t_us_cm)} />
          <Reading label="G" {...joined(scaled(latest?.conductance_s, 'S'))} />
          <Reading label="U" {...joined(scaled(latest?.voltage_v, 'V'))} />
          <Reading label="I" {...joined(scaled(latest?.current_a, 'A'))} />
          <Reading label="T" text={latest?.temperature_c == null ? '—' : `${fixed(latest.temperature_c, 2)} °C`} testId="temperature" />
        </div>
        <Flags flags={latest?.flags ?? []} />
      </section>

      <section className="card stability-card">
        <h2>
          稳定性 <VerdictBadge verdict={running ? qc?.verdict : null} testId="live-verdict" />
        </h2>
        {running && qc ? (
          <>
            <p className="hint">
              已记录 {duration(latest?.t_s)}，按最后 {fixed(lab?.qc_config.window_s, 0)} s 判稳
              {qc.reasons.length > 0 ? `：${qc.reasons.map(reasonLabel).join('、')}` : '：读数稳定，可以停止'}
            </p>
          </>
        ) : (
          <p className="hint">开始测量后，在最后 {fixed(lab?.qc_config.window_s, 0)} s 的窗口上实时判稳；稳定后再停止。</p>
        )}
      </section>

      <section className="card chart-card">
        <h2>{running ? `测量 #${running.id} · ${running.sample_name}` : '实时监视'}</h2>
        <Chart options={chartOptions} data={chartData} height={300} testId="live-chart" />
      </section>

      {lastFinished && !running && (
        <section className="card result-card" data-testid="last-result">
          <h2>
            上次测量 #{lastFinished.id} · {lastFinished.sample_name}{' '}
            <VerdictBadge verdict={lastFinished.qc?.verdict} testId="result-verdict" />
          </h2>
          {lastFinished.qc && <QcSummary qc={lastFinished.qc} />}
          <button type="button" onClick={() => onShowRecord(lastFinished.id)}>
            在记录中查看
          </button>
        </section>
      )}
    </div>
  );
}

function joined(value: { text: string; unit: string }) {
  return { text: value.text === '—' ? '—' : `${value.text} ${value.unit}` };
}

function Reading({ label, text, testId }: { label: string; text: string; testId?: string }) {
  return (
    <div className="reading">
      <span className="label">{label}</span>
      <span className="value" data-testid={testId}>
        {text}
      </span>
    </div>
  );
}
