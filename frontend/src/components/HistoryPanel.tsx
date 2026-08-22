import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ApiClient } from '../services/apiClient';
import type { DataPoint, ExperimentDetail, ExperimentSummary, RawFrame } from '../types/protocol';
import { FitPanel } from './FitPanel';
import { StaticChart } from './StaticChart';
import styles from './HistoryPanel.module.css';

interface Props {
  api: ApiClient | null;
  onClose: () => void;
}

const MAX_CHART_POINTS = 2000;
const STATUS_LABELS: Record<string, string> = {
  running: '进行中',
  stopped: '已停止',
  aborted: '已中止',
  error: '错误',
  idle: '未开始（旧记录）',
};

function fmtStatus(status: string): string {
  return STATUS_LABELS[status] ?? `未知状态（${status}）`;
}

function fmtTime(utc?: string | null): string {
  if (!utc) return '--';
  const d = new Date(utc);
  return Number.isNaN(d.getTime()) ? utc : d.toLocaleString();
}

/**
 * 历史实验面板（Phase 7）：列表 → 详情（样品汇总 + 静态曲线 + 导出）。
 * 仅在 server 模式可用（browser 模式无历史 API）。
 */
export function HistoryPanel({ api, onClose }: Props) {
  const [list, setList] = useState<ExperimentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ExperimentDetail | null>(null);
  const [frames, setFrames] = useState<RawFrame[]>([]);
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    if (!api) return;
    let cancelled = false;
    api
      .listExperiments()
      .then((items) => {
        if (!cancelled) setList(items);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '获取历史列表失败');
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  const openDetail = useCallback(
    async (id: number) => {
      if (!api) return;
      setLoadingDetail(true);
      setError(null);
      try {
        // P2-7 修复：拉取全部帧（而非前 3000 帧），曲线降采样显示、拟合用全量
        const [detail, rawFrames] = await Promise.all([
          api.getExperiment(id),
          api.getAllFrames(id),
        ]);
        setSelected(detail);
        setFrames(rawFrames);
      } catch (err) {
        setError(err instanceof Error ? err.message : '获取实验详情失败');
      } finally {
        setLoadingDetail(false);
      }
    },
    [api],
  );

  // 注意：所有 Hook 必须位于任何 early return 之前，否则 api 变化时 React 会因
  // Hook 数量不一致直接崩溃（"Rendered more hooks than during the previous render"）。
  const chartData: [number, number][] = useMemo(
    // 主显示量 κ25 优先，回退 κ(T)，再回退 V1 legacy（旧库帧）；无效点剔除
    () =>
      frames
        .map((f) => [
          f.t_seconds ?? 0,
          f.kappa_25_us_cm ?? f.kappa_t_us_cm ?? f.legacy_ec_us_cm ?? null,
        ])
        .filter((v): v is [number, number] => v[1] !== null),
    [frames],
  );

  // P2-7：曲线最多降采样到 2000 点显示（保趋势、防卡顿）；拟合仍用全量帧
  const displayData: [number, number][] = useMemo(
    () =>
      chartData.length > MAX_CHART_POINTS
        ? Array.from({ length: MAX_CHART_POINTS }, (_, i) =>
            chartData[Math.floor((i * chartData.length) / MAX_CHART_POINTS)],
          )
        : chartData,
    [chartData],
  );

  // 历史详情拟合用的数据点（复用 FitPanel 化学公式拟合，X 轴可切时间/温度）
  const historyPoints: DataPoint[] = useMemo(
    () => {
      const concentrations = new Map(
        (selected?.samples ?? []).map((sample) => [
          `${sample.sample_id}\u0000${sample.sensor_path_id ?? ''}`,
          sample.concentration_mmol_l,
        ]),
      );
      return frames.map((f) => ({
        t: f.t_seconds ?? 0,
        tc: f.temperature_raw_c ?? null,
        kt: f.kappa_t_us_cm ?? f.legacy_ec_us_cm ?? null,
        k25: f.kappa_25_us_cm ?? null,
        u: f.voltage_raw_v ?? null,
        i: f.current_raw_a !== null && f.current_raw_a !== undefined ? f.current_raw_a * 1000 : null,
        g: f.conductance_s ?? null,
        freq: f.excitation_frequency_hz ?? undefined,
        amp: f.excitation_amplitude_v ?? undefined,
        rangeId: f.range_id ?? undefined,
        sensorPathId: f.sensor_path_id,
        calibrationId: f.calibration_id ?? undefined,
        cellConstant: f.cell_constant_cm_inv ?? null,
        calibrationValidUntil: f.calibration_valid_until_utc ?? null,
        compensationModel: f.compensation_model ?? undefined,
        alphaPerC: f.alpha_per_c ?? null,
        qualityFlags:
          f.quality_flags_list ?? (f.quality_flags ? f.quality_flags.split('|') : undefined),
        concentration:
          concentrations.get(`${f.sample_id ?? ''}\u0000${f.sensor_path_id ?? ''}`) ?? null,
      }));
    },
    [frames, selected],
  );

  if (!api) {
    return (
      <div className={styles.overlay} data-testid="history-panel">
        <div className={styles.modal}>
          <div className={styles.head}>
            <h2>历史实验</h2>
            <button className={styles.close} onClick={onClose} aria-label="关闭">
              ×
            </button>
          </div>
          <p className={styles.hint}>浏览器模拟模式下无历史数据；请切换 server 模式连接后端。</p>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.overlay} data-testid="history-panel">
      <div className={styles.modal}>
        <div className={styles.head}>
          <h2>历史实验</h2>
          <button className={styles.close} onClick={onClose} aria-label="关闭">
            ×
          </button>
        </div>

        {error && <div className={styles.error}>{error}</div>}

        {selected ? (
          <div className={styles.detail}>
            <div className={styles.detailHead}>
              <div>
                <div className={styles.title}>{selected.title}</div>
                <div className={styles.meta}>
                  {selected.experiment_id} · 样品 {selected.sample_id ?? '--'} ·{' '}
                  {selected.sensor_path_id ?? '--'} · {fmtTime(selected.started_at_utc)}
                </div>
                <div className={styles.meta}>
                  状态 {fmtStatus(selected.status)} · 原始帧 {selected.frame_count} · 结束{' '}
                  {fmtTime(selected.ended_at_utc)}
                </div>
              </div>
              <div className={styles.actions}>
                <a
                  className={styles.download}
                  href={api.exportCsvUrl(selected.id)}
                  download
                  data-testid="btn-export-csv"
                >
                  导出 CSV
                </a>
                <a
                  className={styles.download}
                  href={api.exportJsonUrl(selected.id)}
                  download
                >
                  导出 JSON
                </a>
                <button className={styles.back} onClick={() => setSelected(null)}>
                  返回列表
                </button>
              </div>
            </div>

            {selected.samples.length > 0 && (
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>样品</th>
                    <th>链路</th>
                    <th>浓度 (mmol/L)</th>
                    <th>帧数</th>
                    <th>中位数 (μS/cm)</th>
                    <th>均值 (μS/cm)</th>
                    <th>SD</th>
                  </tr>
                </thead>
                <tbody>
                  {selected.samples.map((s) => (
                    <tr key={s.id}>
                      <td>{s.sample_id}</td>
                      <td>{s.sensor_path_id ?? '--'}</td>
                      <td>{s.concentration_mmol_l ?? '--'}</td>
                      <td>{s.frame_count}</td>
                      <td>{s.k25_median?.toFixed(2) ?? '--'}</td>
                      <td>{s.k25_mean?.toFixed(2) ?? '--'}</td>
                      <td>{s.k25_sd?.toFixed(2) ?? '--'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <div className={styles.chartWrap}>
              {loadingDetail ? (
                <div className={styles.hint}>加载中…</div>
              ) : chartData.length > 0 ? (
                <>
                  {chartData.length > MAX_CHART_POINTS && (
                    <div className={styles.hint} data-testid="history-downsample-note">
                      共 {chartData.length} 帧，曲线按 {MAX_CHART_POINTS} 点降采样显示；拟合基于全部{' '}
                      {chartData.length} 帧
                    </div>
                  )}
                  <StaticChart data={displayData} />
                </>
              ) : (
                <div className={styles.hint}>该实验暂无原始帧</div>
              )}
            </div>

            {chartData.length > 0 && (
              <FitPanel api={api} points={historyPoints} testIdPrefix="hist-fit" key={selected.id} />
            )}
          </div>
        ) : (
          <div className={styles.list} data-testid="history-list">
            {loadingDetail ? (
              <div className={styles.hint} data-testid="history-detail-loading">
                正在加载实验详情…
              </div>
            ) : list === null ? (
              <div className={styles.hint}>加载中…</div>
            ) : list.length === 0 ? (
              <div className={styles.hint}>暂无历史实验，先运行一轮实验吧。</div>
            ) : (
              list.map((item) => (
                <button
                  key={item.id}
                  className={styles.row}
                  onClick={() => openDetail(item.id)}
                  data-testid={`history-item-${item.id}`}
                >
                  <span className={styles.rowMain}>
                    <span className={styles.title}>{item.title}</span>
                    <span className={styles.meta}>
                      {item.experiment_id} · 样品 {item.sample_id ?? '--'} · {fmtStatus(item.status)}
                    </span>
                  </span>
                  <span className={styles.meta}>
                    {item.frame_count} 帧 · {fmtTime(item.started_at_utc)}
                  </span>
                </button>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
