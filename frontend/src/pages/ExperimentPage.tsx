import { useCallback, useMemo, useState } from 'react';
import { config } from '../config/config';
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
    persistDegraded,
    setActionError,
    start,
    stop,
    reset,
    restart,
    canStart,
    canStop,
    canReset,
  } = useExperiment(bridge);
  const {
    pointsRef,
    count,
    revision,
    latest,
    clearPoints,
    hydrateFromFrames,
    bindExperiment,
  } = useRealtimeData(bridge);
  const ivAnalysis = useIVAnalysis(pointsRef, revision);

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
    const res = await start(options);
    if (!res.ok) return;
    if (res.resumed && res.experiment_id != null && bridge.api) {
      try {
        // 缓冲只留最后 2 万点：只取尾部（R3-6）。旧实现拉前 10 万帧——长实验取到的是开头，
        // 传输几十 MB 后又被裁掉大半，超 10 万帧时曲线中间还会断档。
        const frames = await bridge.api.getFrames(res.experiment_id, config.chart.maxPoints, 'tail');
        hydrateFromFrames(frames, res.experiment_id);
      } catch {
        /* 续跑时灌入历史帧失败则继续用内存缓冲 */
      }
      return;
    }
    // 新实验：缓冲按实验隔离，id 变了才清空。WS 状态帧/数据帧通常已先一步对齐，
    // 这里是断线等情况下的兜底。旧实现等响应回来再 clearPoints()，会把已先到的新实验首批帧一并抹掉。
    bindExperiment(res.experiment_id);
  }, [bindExperiment, bridge.api, hydrateFromFrames, start, startOptions]);

  const handleClear = useCallback(() => {
    // reset 失败（后端拒绝/网络断开）时本地缓冲不能先清，否则 UI 与服务器状态错位
    void reset().then((res) => {
      if (res.ok) clearPoints();
    });
  }, [clearPoints, reset]);

  // 帧的 timestamp 就是实验开始后的运行时间（暂停不计）：续跑水合、旁观端中途加入都不影响
  const duration = latest && Number.isFinite(latest.t) ? Math.max(0, latest.t) : 0;
  // 采样率按缓冲首末点跨度算：缓冲封顶后 count 不再增长而 duration 持续变大，
  // 用实验总时长会把采样率越算越低；封顶时缓冲会从前端裁剪，首末跨度与 count 同步。
  // 首/末点 t 必须有限（P2-11）：NaN 会让 rateSpan 为 NaN → 恒显 "--"
  const ptsForRate = pointsRef.current;
  const firstT = ptsForRate[0]?.t;
  const rateSpan =
    latest && firstT != null && Number.isFinite(firstT) && Number.isFinite(latest.t)
      ? Math.max(0, latest.t - firstT)
      : 0;
  const sampleRateHz = count > 1 && rateSpan > 0.2 ? (count - 1) / rateSpan : null;
  const shownError = actionError ?? error;
  const currentDisplay = latest?.current_raw_a != null ? formatCurrentA(latest.current_raw_a) : null;
  const simulated = latest?.quality_flags?.includes('SIMULATED') ?? false;
  const persistDropped = latest?.quality_flags?.includes('PERSIST_DROPPED') ?? false;
  const showPersistWarn = persistDegraded || persistDropped;
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
        {showPersistWarn ? (
          <span className={styles.persistWarn} data-testid="persist-degraded-badge">
            {' '}
            · 落库失败（请重启后端）
          </span>
        ) : null}
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
