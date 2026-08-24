import { useMemo, useState } from 'react';
import { getBridge } from '../services';
import { useConnection } from '../hooks/useConnection';
import { useExperiment } from '../hooks/useExperiment';
import { useRealtimeData } from '../hooks/useRealtimeData';
import { ExperimentInfo } from '../components/ExperimentInfo';
import { StatusBadge } from '../components/StatusBadge';
import { ConnectionPanel } from '../components/ConnectionPanel';
import { ErrorBanner } from '../components/ErrorBanner';
import { ControlBar } from '../components/ControlBar';
import { ValueDisplay } from '../components/ValueDisplay';
import { DataStats } from '../components/DataStats';
import { RealTimeChart } from '../components/RealTimeChart';
import { ResultPanel } from '../components/ResultPanel';
import { HistoryPanel } from '../components/HistoryPanel';
import styles from './ExperimentPage.module.css';

const EXPERIMENT_TITLE = '溶液导电性相对比较实验';

/**
 * 主实验页：实验信息 + 实时数值 + 实时曲线 + 控制 + 连接状态 + 结果区 + 历史实验（Phase 7）。
 * 本页只做组合编排，业务逻辑都在 hooks/services 中。
 */
export function ExperimentPage() {
  const bridge = useMemo(() => getBridge(), []);
  const [sampleIdInput, setSampleIdInput] = useState('BLANK');
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
    restart,
    canStart,
    canStop,
    canReset,
  } = useExperiment(bridge);
  const { pointsRef, count, latest, runStartTRef } = useRealtimeData(bridge);

  const duration =
    latest && runStartTRef.current !== null ? Math.max(0, latest.t - runStartTRef.current) : 0;

  const shownError = actionError ?? error;

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
            paused={status === 'stopped'}
            onStart={() => start({ sample_id: sampleIdInput.trim() || undefined })}
            onStop={stop}
            onReset={() => restart({ sample_id: sampleIdInput.trim() || undefined })}
          />
          <label className={styles.sampleInputWrap}>
            <span className={styles.sampleLabel}>样品编号</span>
            <input
              className={styles.sampleInput}
              value={sampleIdInput}
              onChange={(e) => setSampleIdInput(e.target.value)}
              placeholder="如 NACL_004 / BLANK"
              disabled={status === 'running'}
              data-testid="input-sample"
            />
          </label>
        </div>
        <ConnectionPanel status={connStatus} mode={bridge.mode} onReconnect={manualReconnect} />
      </section>

      <section className={styles.values}>
        <ValueDisplay
          label="电极温度"
          value={latest?.tc ?? null}
          unit="°C"
          precision={2}
          testId="value-temperature"
        />
        {/* REQ-U-001 分层显示：原始量 / 计算量 / 温补量（后端 v2+ 才下发，缺省显示 --） */}
        <ValueDisplay
          label="原始电压 U"
          value={latest?.voltage_raw_v ?? null}
          unit="V"
          precision={6}
          testId="value-voltage"
        />
        <ValueDisplay
          label="原始电流 I"
          value={latest?.current_raw_a != null ? latest.current_raw_a * 1e6 : null}
          unit="μA"
          precision={3}
          testId="value-current"
        />
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
        <ValueDisplay
          label="κ25"
          value={latest?.kappa_25_us_cm ?? null}
          unit="μS/cm"
          precision={1}
          testId="value-kappa25"
        />
      </section>

      <section className={styles.chartCard}>
        <div className={styles.chartHead}>
          <h2 className={styles.chartTitle}>实时曲线 · EC-时间</h2>
          <DataStats pointCount={count} durationSec={duration} />
        </div>
        <div className={styles.chartBody}>
          <RealTimeChart pointsRef={pointsRef} />
        </div>
      </section>

      <ResultPanel
        pointsRef={pointsRef}
        status={status}
        count={count}
        experimentId={experimentId}
        sampleId={sampleId}
        api={bridge.api}
      />

      {historyOpen && <HistoryPanel api={bridge.api} onClose={() => setHistoryOpen(false)} />}
    </div>
  );
}
