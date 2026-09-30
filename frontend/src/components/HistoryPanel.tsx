import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ApiClient } from '../services/apiClient';
import type { DataPoint, ExperimentDetail, ExperimentSummary, RawFrame } from '../types/protocol';
import { downsample } from '../lib/downsample';
import { MAX_FIT_POINTS } from '../lib/fitPoints';
import { rawFrameToPoint } from '../lib/ivAnalysis';
import { fmtFixed } from '../lib/units';
import { CalibrationPanel } from './CalibrationPanel';
import { ExportLink } from './ExportLink';
import { FitPanel } from './FitPanel';
import { StaticChart } from './StaticChart';
import styles from './HistoryPanel.module.css';

interface Props {
  api: ApiClient;
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
 */
export function HistoryPanel({ api, onClose }: Props) {
  const [list, setList] = useState<ExperimentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ExperimentDetail | null>(null);
  const [frames, setFrames] = useState<RawFrame[]>([]);
  const [loadingDetail, setLoadingDetail] = useState(false);
  // 列表视图的两种模式：逐个浏览详情 / 跨实验浓度标定（勾选多个实验一起拟合）
  const [mode, setMode] = useState<'browse' | 'calibrate'>('browse');
  // 竞态保护：快速连点两个实验时，旧响应不得覆盖新选中的详情（同 FitPanel 模式）
  const detailRequestIdRef = useRef(0);

  const loadList = useCallback(() => {
    setError(null);
    setList(null);
    api
      .listExperiments()
      .then((items) => setList(items))
      .catch((err) => {
        setList([]);
        setError(err instanceof Error ? err.message : '获取历史列表失败');
      });
  }, [api]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  const openDetail = useCallback(
    async (id: number) => {
      const requestId = ++detailRequestIdRef.current;
      setLoadingDetail(true);
      setError(null);
      try {
        // 全实验等间隔抽样至多 2 万帧（= 拟合点上限，R3-6）。旧实现拉前 10 万帧：超长实验
        // 只看得到开头、提示却写“拟合基于全部帧”，单次传输也可达 63MB。
        const [detail, rawFrames] = await Promise.all([
          api.getExperiment(id),
          api.getFrames(id, MAX_FIT_POINTS, 'even'),
        ]);
        if (requestId !== detailRequestIdRef.current) return;
        setSelected(detail);
        setFrames(rawFrames);
      } catch (err) {
        if (requestId !== detailRequestIdRef.current) return;
        setError(err instanceof Error ? err.message : '获取实验详情失败');
      } finally {
        if (requestId === detailRequestIdRef.current) setLoadingDetail(false);
      }
    },
    [api],
  );

  // 注意：所有 Hook 必须位于任何 early return 之前，否则 api 变化时 React 会因
  // Hook 数量不一致直接崩溃（"Rendered more hooks than during the previous render"）。
  // 历史详情拟合用的数据点（复用 FitPanel 化学公式拟合，X 轴可切时间/温度/浓度）。
  // t_seconds 为 null 的帧由 rawFrameToPoint 给 NaN，交给下游过滤（T-04）。
  const historyPoints: DataPoint[] = useMemo(() => {
    const concBySample = new Map<string, number>();
    for (const s of selected?.samples ?? []) {
      if (s.concentration_mmol_l != null && Number.isFinite(s.concentration_mmol_l)) {
        concBySample.set(s.sample_id, s.concentration_mmol_l);
      }
    }
    return frames.map((f) => ({
      ...rawFrameToPoint(f),
      // 浓度 0（空白样）是合法标定点，必须保留：`||` 会把 0 吞成 undefined
      concentration: f.sample_id != null ? concBySample.get(f.sample_id) : undefined,
    }));
  }, [frames, selected]);

  const chartData: [number, number][] = useMemo(
    () =>
      historyPoints.flatMap((p) =>
        p.ec !== null && Number.isFinite(p.ec) && Number.isFinite(p.t)
          ? [[p.t, p.ec] as [number, number]]
          : [],
      ),
    [historyPoints],
  );

  // P2-7：曲线最多降采样到 2000 点显示（保趋势、防卡顿）；拟合用已加载的全部帧（≤2 万，全实验等间隔抽样）
  const displayData = useMemo(() => downsample(chartData, MAX_CHART_POINTS), [chartData]);

  return (
    <div className={styles.overlay} data-testid="history-panel">
      <div className={styles.modal}>
        <div className={styles.head}>
          <h2>{mode === 'calibrate' && !selected ? '跨实验浓度标定' : '历史实验'}</h2>
          <div className={styles.headActions}>
            {!selected && (
              <button
                type="button"
                className={styles.back}
                onClick={() => setMode((m) => (m === 'calibrate' ? 'browse' : 'calibrate'))}
                data-testid="btn-calibration-mode"
              >
                {mode === 'calibrate' ? '返回列表' : '跨实验标定'}
              </button>
            )}
            <button className={styles.close} onClick={onClose} aria-label="关闭">
              ×
            </button>
          </div>
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
                  {selected.calibrations?.[0]?.calibration_id
                    ? ` · 校准 ${selected.calibrations[0].calibration_id}`
                    : ''}
                </div>
              </div>
              <div className={styles.actions}>
                <ExportLink
                  api={api}
                  experimentId={selected.id}
                  format="csv"
                  className={styles.download}
                  testId="btn-export-csv"
                  onError={setError}
                />
                <ExportLink
                  api={api}
                  experimentId={selected.id}
                  format="json"
                  className={styles.download}
                  testId="btn-export-json"
                  onError={setError}
                />
                <button className={styles.back} onClick={() => setSelected(null)}>
                  返回列表
                </button>
              </div>
            </div>

            {selected.samples.length > 0 && (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>样品</th>
                    <th>链路</th>
                    <th>浓度 (mmol/L)</th>
                    <th>帧数</th>
                    <th>QC</th>
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
                      <td>{s.qc_status ?? '--'}</td>
                      <td>{fmtFixed(s.k25_median, 2)}</td>
                      <td>{fmtFixed(s.k25_mean, 2)}</td>
                      <td>{fmtFixed(s.k25_sd, 2)}</td>
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
                  {(selected.frame_count > frames.length ||
                    chartData.length > MAX_CHART_POINTS) && (
                    <div className={styles.hint} data-testid="history-downsample-note">
                      {selected.frame_count > frames.length
                        ? `共 ${selected.frame_count} 帧，按全实验等间隔抽样 ${frames.length} 帧（保留首末帧）用于拟合；`
                        : `共 ${chartData.length} 帧，拟合基于全部帧；`}
                      曲线按 {MAX_CHART_POINTS} 点降采样显示
                    </div>
                  )}
                  <StaticChart data={displayData} />
                </>
              ) : (
                <div className={styles.hint}>该实验暂无原始帧</div>
              )}
            </div>

            {chartData.length > 0 && (
              <FitPanel
                api={api}
                points={historyPoints}
                experimentId={selected.id}
                testIdPrefix="hist-fit"
                key={selected.id}
              />
            )}
          </div>
        ) : mode === 'calibrate' ? (
          list === null ? (
            <div className={styles.hint}>加载中…</div>
          ) : (
            <CalibrationPanel api={api} experiments={list} />
          )
        ) : (
          <div className={styles.list} data-testid="history-list">
            {loadingDetail ? (
              <div className={styles.hint} data-testid="history-detail-loading">
                正在加载实验详情…
              </div>
            ) : list === null ? (
              <div className={styles.hint}>加载中…</div>
            ) : list.length === 0 ? (
              <div className={styles.hint}>
                {error ? (
                  <button type="button" className={styles.back} onClick={loadList}>
                    重试
                  </button>
                ) : (
                  '暂无历史实验，先运行一轮实验吧。'
                )}
              </div>
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
