import { useCallback, useMemo, useState } from 'react';
import { getBridge } from '../services';
import { useConnection } from '../hooks/useConnection';
import { useExperiment } from '../hooks/useExperiment';
import { useRealtimeData } from '../hooks/useRealtimeData';
import { useIVAnalysis } from '../hooks/useIVAnalysis';
import { ExperimentInfo } from '../components/ExperimentInfo';
import { StatusBadge } from '../components/StatusBadge';
import { ConnectionPanel } from '../components/ConnectionPanel';
import { ErrorBanner } from '../components/ErrorBanner';
import { ControlBar } from '../components/ControlBar';
import { ValueDisplay } from '../components/ValueDisplay';
import { DataStats } from '../components/DataStats';
import { WaveformChart } from '../components/WaveformChart';
import { IVChart } from '../components/IVChart';
import { ExperimentResultCard } from '../components/ExperimentResultCard';
import { SolutionCompare } from '../components/SolutionCompare';
import { DiagnosticsPanel } from '../components/DiagnosticsPanel';
import { ResultPanel } from '../components/ResultPanel';
import { HistoryPanel } from '../components/HistoryPanel';
import { formatCurrentA } from '../lib/units';
import styles from './ExperimentPage.module.css';

const EXPERIMENT_TITLE = '溶液导电性相对比较实验';

function parseConcentrationMmolL(raw: string): { value?: number; error?: string } {
  const trimmed = raw.trim();
  if (trimmed === '') return {};
  const n = Number(trimmed);
  if (!Number.isFinite(n) || n < 0) {
    return { error: '浓度须为 ≥0 的数字（mmol/L）' };
  }
  return { value: n };
}

/**
 * 主实验页：实时 V/I → I–V 特性 → 溶液比较。
 * EC-t 与化学拟合放在诊断/结果区，不再作为核心图。
 */
export function ExperimentPage() {
  const bridge = useMemo(() => getBridge(), []);
  const [sampleIdInput, setSampleIdInput] = useState('BLANK');
  const [concentrationInput, setConcentrationInput] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);

  const { connStatus, error, setError, manualReconnect } = useConnection(bridge);
  const {
    status,
    startedAt,
    experimentId,
    sampleId,
    busy,
    actionError,
    setActionError,
    start,
    stop,
    reset,
    restart,
    canStart,
    canStop,
    canReset,
  } = useExperiment(bridge);
  const { pointsRef, count, latest, runStartTRef, clearPoints, hydrateFromFrames } =
    useRealtimeData(bridge);
  const ivAnalysis = useIVAnalysis(pointsRef, count);

  const startOptions = useCallback(() => {
    const parsed = parseConcentrationMmolL(concentrationInput);
    if (parsed.error) {
      setActionError(parsed.error);
      return null;
    }
    return {
      sample_id: sampleIdInput.trim() || undefined,
      concentration_mmol_l: parsed.value,
    };
  }, [concentrationInput, sampleIdInput, setActionError]);

  const handleStart = useCallback(async () => {
    const options = startOptions();
    if (!options) return;
    const previousId = experimentId;
    const res = await start(options);
    if (!res || !('ok' in res) || !res.ok) return;
    if (res.resumed && res.experiment_id != null && bridge.api) {
      try {
        const frames = await bridge.api.getFrames(res.experiment_id, 100_000);
        hydrateFromFrames(frames);
      } catch {
        /* 续跑时灌入历史帧失败则继续用内存缓冲 */
      }
      return;
    }
    if (previousId != null && res.experiment_id !== previousId) {
      clearPoints();
    }
  }, [bridge.api, clearPoints, experimentId, hydrateFromFrames, start, startOptions]);

  const handleClear = useCallback(() => {
    void reset();
    clearPoints();
  }, [clearPoints, reset]);

  const duration =
    latest && runStartTRef.current !== null ? Math.max(0, latest.t - runStartTRef.current) : 0;
  const sampleRateHz = count > 1 && duration > 0.2 ? (count - 1) / duration : null;
  const shownError = actionError ?? error;
  const currentDisplay = latest?.current_raw_a != null ? formatCurrentA(latest.current_raw_a) : null;
  const simulated = latest?.quality_flags?.includes('SIMULATED') ?? false;
  const displayedSample = sampleId || sampleIdInput;

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <ExperimentInfo title={EXPERIMENT_TITLE} startedAt={startedAt} />
        <div className={styles.headerRight}>
          <button
            type="button"
            className={styles.historyBtn}
            onClick={() => setHistoryOpen(true)}
            data-testid="btn-history"
          >
            历史实验
          </button>
          <StatusBadge status={status} />
        </div>
      </header>

      <p className={styles.story}>
        施加交流激励 → 测量电流 → 得到导电能力 → 比较不同溶液
      </p>

      <ErrorBanner
        message={shownError}
        onDismiss={() => {
          setError(null);
          setActionError(null);
        }}
      />

      <section className={styles.controls}>
        <div className={styles.controlLeft}>
          <ControlBar
            busy={busy}
            canStart={canStart}
            canStop={canStop}
            canReset={canReset}
            canClear={status !== 'running' && (count > 0 || status === 'stopped')}
            paused={status === 'stopped'}
            onStart={() => void handleStart()}
            onStop={stop}
            onReset={() => {
              const options = startOptions();
              if (options) void restart(options);
            }}
            onClear={handleClear}
          />
          <label className={styles.sampleInputWrap}>
            <span className={styles.sampleLabel}>溶液 / 样品</span>
            <input
              className={styles.sampleInput}
              value={sampleIdInput}
              onChange={(e) => setSampleIdInput(e.target.value)}
              placeholder="如 NACL_004 / BLANK"
              disabled={status === 'running'}
              data-testid="input-sample"
            />
          </label>
          <label className={styles.sampleInputWrap}>
            <span className={styles.sampleLabel}>浓度 mmol/L</span>
            <input
              className={styles.concentrationInput}
              type="number"
              min="0"
              step="any"
              inputMode="decimal"
              value={concentrationInput}
              onChange={(e) => setConcentrationInput(e.target.value)}
              placeholder="可选"
              disabled={status === 'running'}
              data-testid="input-concentration"
            />
          </label>
        </div>
        <ConnectionPanel status={connStatus} mode={bridge.mode} onReconnect={manualReconnect} />
      </section>

      <p className={styles.metaRow} data-testid="experiment-meta">
        实验编号 {experimentId ?? '--'}
        {displayedSample ? ` · 溶液 ${displayedSample}` : ''}
        {simulated ? ' · 模拟设备' : ''}
      </p>

      <section className={styles.values}>
        <ValueDisplay
          label="电极温度"
          value={latest?.tc ?? null}
          unit="°C"
          precision={2}
          testId="value-temperature"
        />
        <ValueDisplay
          label="当前电压"
          value={latest?.voltage_raw_v ?? null}
          unit="V"
          precision={4}
          testId="value-voltage"
        />
        <ValueDisplay
          label="当前电流"
          value={currentDisplay ? currentDisplay.value : null}
          unit={currentDisplay?.unit ?? 'μA'}
          precision={3}
          testId="value-current"
        />
        <ValueDisplay
          label="电导率 κ25"
          value={latest?.kappa_25_us_cm ?? latest?.ec ?? null}
          unit="μS/cm"
          precision={1}
          testId="value-kappa25"
        />
      </section>

      <section className={styles.chartCard}>
        <div className={styles.chartHead}>
          <div>
            <h2 className={styles.chartTitle}>实时测量</h2>
            <p className={styles.chartHint}>交流激励下的电压、电流采样。用于看噪声和是否稳定。</p>
          </div>
          <DataStats
            pointCount={count}
            durationSec={duration}
            sampleRateHz={sampleRateHz}
            excitationFreqHz={latest?.excitation_frequency_hz}
            excitationAmpV={latest?.excitation_amplitude_v}
          />
        </div>
        <div className={styles.chartBodyWaveform}>
          <WaveformChart pointsRef={pointsRef} />
        </div>
      </section>

      <section className={styles.chartCard}>
        <div className={styles.chartHead}>
          <div>
            <h2 className={styles.chartTitle}>I–V 特性</h2>
            <p className={styles.chartHint}>
              这是本实验的核心图。只有电压真正扫开、且近似直线时，才用斜率当电导。
            </p>
          </div>
        </div>
        {ivAnalysis.n > 0 && (
          <div className={styles.resultCardWrap}>
            <ExperimentResultCard analysis={ivAnalysis} sampleId={displayedSample} />
          </div>
        )}
        <div className={styles.chartBodyIV}>
          <IVChart pointsRef={pointsRef} analysis={ivAnalysis} status={status} />
        </div>
      </section>

      <section className={styles.chartCard}>
        <div className={styles.chartHead}>
          <div>
            <h2 className={styles.chartTitle}>不同溶液电导率比较</h2>
            <p className={styles.chartHint}>
              从打开本页后的第一次实验开始。换溶液再测会多一根柱，不带入历史记录。
            </p>
          </div>
        </div>
        <SolutionCompare
          status={status}
          experimentId={experimentId}
          sampleId={displayedSample}
          live={ivAnalysis}
          simulated={simulated}
        />
      </section>

      <ResultPanel
        pointsRef={pointsRef}
        status={status}
        count={count}
        experimentId={experimentId}
        sampleId={sampleId}
        api={bridge.api}
      />

      <DiagnosticsPanel
        pointsRef={pointsRef}
        extras={
          <>
            <ValueDisplay
              label="电导 G"
              value={latest?.conductance_s != null ? latest.conductance_s * 1e6 : null}
              unit="μS"
              precision={3}
              testId="value-conductance"
            />
            <ValueDisplay
              label="κ(T)"
              value={latest?.kappa_t_us_cm ?? null}
              unit="μS/cm"
              precision={1}
              testId="value-kappa-t"
            />
          </>
        }
      />

      {historyOpen && <HistoryPanel api={bridge.api} onClose={() => setHistoryOpen(false)} />}
    </div>
  );
}
