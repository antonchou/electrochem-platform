import { useEffect, useMemo, useRef, useState } from 'react';
import type { DataPoint, FitAxis, FitResponse, FitResultItem } from '../types/protocol';
import type { ApiClient } from '../services/apiClient';
import { buildFitPoints, fitCandidates } from '../lib/fitPoints';
import { StaticChart, type ChartOverlay } from './StaticChart';
import styles from './FitPanel.module.css';

interface Props {
  api: ApiClient | null;
  /** 数据点（t 秒 / tc °C / ec μS·cm⁻¹），按 X 轴语义自动取 x */
  points: DataPoint[];
  /** 当前实验 id：提供则拟合结果入库 */
  experimentId?: number | null;
  /** data-testid 前缀，供多处使用互不冲突 */
  testIdPrefix?: string;
  /** 开始拟合按钮的 testid（ResultPanel 保持历史值 btn-fit） */
  btnTestId?: string;
  /** 可选的 X 轴（默认三轴全开）；跨实验标定只开浓度轴 */
  axes?: FitAxis[];
  /** 自定义拟合请求（默认 /api/analysis/fit；跨实验标定改走 /api/analysis/calibration） */
  fitRunner?: (points: [number, number][], models: string[], axis: FitAxis) => Promise<FitResponse>;
}

const ALL_AXES: FitAxis[] = ['time', 'temperature', 'concentration'];

/** X 轴语义 → 该轴可用的化学模型池（与后端 analysis.MODELS 对齐） */
const AXIS_MODELS: Record<FitAxis, { key: string; label: string }[]> = {
  time: [
    { key: 'linear', label: '线性' },
    { key: 'quadratic', label: '二次多项式' },
    { key: 'first_order', label: '一阶指数饱和' },
    { key: 'exponential', label: '指数' },
    { key: 'logarithmic', label: '对数' },
    { key: 'power', label: '幂函数' },
  ],
  temperature: [
    { key: 'linear', label: '线性温补' },
    { key: 'quadratic', label: '二次多项式' },
    { key: 'arrhenius', label: 'Arrhenius' },
  ],
  concentration: [
    { key: 'linear', label: '线性标定' },
    { key: 'quadratic', label: '二次多项式' },
    { key: 'kohlrausch', label: 'Kohlrausch' },
  ],
};

const AXIS_LABEL: Record<FitAxis, string> = {
  time: '时间 t / s',
  temperature: '温度 T / °C',
  concentration: '浓度 c / mmol·L⁻¹',
};

/** 拟合曲线图（StaticChart）的 X 轴名称 */
const AXIS_CHART_LABEL: Record<FitAxis, string> = {
  time: '时间 (s)',
  temperature: '温度 (°C)',
  concentration: '浓度 (mmol/L)',
};

const CURVE_COLORS = ['#16a34a', '#d97706', '#7c3aed', '#dc2626', '#0891b2', '#db2777'];
const ARRHENIUS_MIN_TEMPERATURE_SPAN_C = 1.0;

function fmtParams(params: Record<string, number>): string {
  return Object.entries(params)
    .map(([k, v]) => `${k}=${v.toExponential(4)}`)
    .join(', ');
}

/**
 * 化学公式拟合面板：X 轴语义（时间/温度/浓度）→ 模型池 → 拟合 → 结果表 + 曲线叠加。
 * 供结果区（ResultPanel）与历史详情（HistoryPanel）复用；后端走 /api/analysis/fit。
 */
export function FitPanel({
  api,
  points,
  experimentId,
  testIdPrefix = 'fit',
  btnTestId,
  axes = ALL_AXES,
  fitRunner,
}: Props) {
  const [xAxis, setXAxis] = useState<FitAxis>(axes[0]);
  const [selectedModels, setSelectedModels] = useState<string[]>(
    AXIS_MODELS[axes[0]].map((m) => m.key),
  );
  const [fitResults, setFitResults] = useState<FitResultItem[] | null>(null);
  const [fitLoading, setFitLoading] = useState(false);
  const [fitError, setFitError] = useState<string | null>(null);
  const fitRequestIdRef = useRef(0);

  // 按 X 轴语义构造 (x, y)：时间轴取 t，温度轴取帧内温度 tc；
  // 浓度轴取 p.concentration。单次实验通常只有一种浓度，Kohlrausch 需要 ≥3 个不同 c。
  const uniqueConcentrations = useMemo(() => {
    const values = new Set<number>();
    for (const p of points) {
      if (p.concentration != null && Number.isFinite(p.concentration)) values.add(p.concentration);
    }
    return values;
  }, [points]);
  const hasRealConcentration = uniqueConcentrations.size > 0;
  const hasUsableConcentrationAxis = uniqueConcentrations.size >= 3;

  const fitPoints: [number, number][] = useMemo(() => buildFitPoints(points, xAxis), [points, xAxis]);
  // 未截断的候选点数：超过后端 2 万点上限被降采样时用于提示（T-02）
  const candidateCount = useMemo(() => fitCandidates(points, xAxis).length, [points, xAxis]);
  const fitDownsampled = candidateCount > fitPoints.length;
  const arrheniusUnavailable = useMemo(() => {
    if (xAxis !== 'temperature' || !selectedModels.includes('arrhenius')) return false;
    const temperatures = fitPoints.map(([temperature]) => temperature);
    return (
      new Set(temperatures).size < 3 ||
      Math.max(...temperatures) - Math.min(...temperatures) < ARRHENIUS_MIN_TEMPERATURE_SPAN_C
    );
  }, [fitPoints, selectedModels, xAxis]);

  const runFit = async () => {
    if (!api || fitPoints.length < 3 || selectedModels.length === 0) return;
    if (xAxis === 'concentration' && !hasUsableConcentrationAxis) return;
    const requestId = ++fitRequestIdRef.current;
    setFitLoading(true);
    setFitError(null);
    const client = api;
    const run =
      fitRunner ??
      ((pairs: [number, number][], models: string[], axis: FitAxis) =>
        client.fitPoints(pairs, models, axis, experimentId));
    try {
      const res = await run(fitPoints, selectedModels, xAxis);
      if (requestId !== fitRequestIdRef.current) return;
      setFitResults(res.models);
    } catch (err) {
      if (requestId !== fitRequestIdRef.current) return;
      setFitError(err instanceof Error ? err.message : '拟合失败');
    } finally {
      if (requestId === fitRequestIdRef.current) setFitLoading(false);
    }
  };

  const invalidateFit = () => {
    // 允许用户在慢请求期间调整条件；旧响应返回后不得覆盖当前选择。
    fitRequestIdRef.current += 1;
    setFitLoading(false);
    setFitResults(null);
    setFitError(null);
  };

  // 数据一变旧拟合即失效（T-03）：points 内容变化时清空上一段数据的拟合结论
  // （最优模型 / R² / 叠加曲线），避免数据更新后残留误导结果。
  // 依赖是"内容签名"而非数组引用（P2-1）：父组件重建等内容数组（如 ResultPanel
  // 停止后详情刷新导致 sample 引用更新）不应误清用户刚算出的结果；
  // 内容真实变化（追加帧 / 换数据集）则必须失效。签名 = 点数 + 首末点时间。
  const pointsSignature = `${points.length}|${points[0]?.t ?? ''}|${points[points.length - 1]?.t ?? ''}`;
  useEffect(() => {
    invalidateFit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pointsSignature]);

  const toggleModel = (key: string) => {
    setSelectedModels((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    );
    invalidateFit();
  };

  const switchAxis = (axis: FitAxis) => {
    if (axis === xAxis) return;
    setXAxis(axis);
    setSelectedModels(AXIS_MODELS[axis].map((m) => m.key));
    invalidateFit();
  };

  const canFit =
    api !== null &&
    fitPoints.length >= 3 &&
    selectedModels.length > 0 &&
    !fitLoading &&
    !(xAxis === 'concentration' && !hasUsableConcentrationAxis);

  // 叠加到曲线图上的拟合曲线（按 R² 从高到低，最多展示前 4 条）
  const overlays: ChartOverlay[] = useMemo(() => {
    if (!fitResults) return [];
    return fitResults.slice(0, 4).map((r, i) => ({
      name: `${r.label} (R²=${r.r2.toFixed(4)})`,
      data: r.fitted,
      color: CURVE_COLORS[i % CURVE_COLORS.length],
    }));
  }, [fitResults]);

  return (
    <div className={styles.reserved} data-testid={`${testIdPrefix}-area`}>
      <div className={styles.fitHead}>
        <span className={styles.reservedTitle}>化学公式拟合</span>
        <span className={styles.reservedHint}>按 X 轴物理含义选择模型池，按 R² 自动排序</span>
      </div>

      <div className={styles.axisRow} data-testid={`${testIdPrefix}-axis`}>
        <span className={styles.axisLabel}>X 轴</span>
        {axes.map((axis) => (
          <button
            key={axis}
            type="button"
            className={`${styles.axisChip} ${xAxis === axis ? styles.axisChipActive : ''}`}
            onClick={() => switchAxis(axis)}
            data-testid={`${testIdPrefix}-axis-${axis}`}
          >
            {AXIS_LABEL[axis]}
          </button>
        ))}
      </div>

      {xAxis === 'concentration' && !hasRealConcentration && (
        <div className={styles.axisNote} data-testid={`${testIdPrefix}-concentration-note`}>
          浓度轴需真实浓度数据：开始实验时填写浓度 mmol/L；当前数据无浓度字段，「开始拟合」已禁用。
          多个浓度的标定曲线请用「历史实验 → 跨实验标定」
        </div>
      )}
      {xAxis === 'concentration' && hasRealConcentration && !hasUsableConcentrationAxis && (
        <div className={styles.axisNote} data-testid={`${testIdPrefix}-concentration-note`}>
          当前数据浓度为 {Array.from(uniqueConcentrations).join(', ')} mmol/L，仅{' '}
          {uniqueConcentrations.size} 种。浓度轴拟合需要 ≥3 个不同浓度：单个实验只有一种浓度，
          请在「历史实验 → 跨实验标定」里组合多个实验
        </div>
      )}

      {!api && (
        <div className={styles.axisNote} data-testid={`${testIdPrefix}-no-api-note`}>
          浏览器模拟模式下无后端拟合接口，「开始拟合」不可用；请切换 server 模式连接后端。
        </div>
      )}

      {arrheniusUnavailable && (
        <div className={styles.axisNote} data-testid={`${testIdPrefix}-arrhenius-note`}>
          当前温度跨度不足 {ARRHENIUS_MIN_TEMPERATURE_SPAN_C.toFixed(1)} °C，Arrhenius
          活化能结果将跳过；线性温补与二次模型仍可计算。
        </div>
      )}

      {fitDownsampled && (
        <div className={styles.axisNote} data-testid={`${testIdPrefix}-downsample-note`}>
          共 {candidateCount} 点，超过拟合上限 {fitPoints.length} 点，已等间隔降采样（保留首末点）。
        </div>
      )}

      <div className={styles.modelRow}>
        {AXIS_MODELS[xAxis].map((m) => {
          const active = selectedModels.includes(m.key);
          return (
            <button
              key={m.key}
              type="button"
              className={`${styles.modelChip} ${active ? styles.modelChipActive : ''}`}
              onClick={() => toggleModel(m.key)}
              data-testid={`${testIdPrefix}-model-${m.key}`}
            >
              {m.label}
            </button>
          );
        })}
        <button
          type="button"
          className={styles.fitBtn}
          onClick={runFit}
          disabled={!canFit}
          data-testid={btnTestId ?? `${testIdPrefix}-btn-fit`}
        >
          {fitLoading ? '拟合中…' : '开始拟合'}
        </button>
      </div>

      {fitError && <div className={styles.fitError}>{fitError}</div>}

      {fitResults && fitResults.length === 0 && (
        <div className={styles.axisNote} data-testid={`${testIdPrefix}-empty`}>
          所选模型均未产生有效拟合结果：数据点不足或不满足模型约束（如对数/幂函数要求 x{'>'}0）。
        </div>
      )}

      {fitResults && fitResults.length > 0 && (
        <div className={styles.fitResults} data-testid={`${testIdPrefix}-results`}>
          <table className="data-table">
            <thead>
              <tr>
                <th>公式</th>
                <th>参数</th>
                <th>R²</th>
                <th>RMSE</th>
                <th>MAE</th>
                <th>AICc</th>
                <th>点数</th>
              </tr>
            </thead>
            <tbody>
              {fitResults.map((r, i) => (
                <tr key={r.model} className={i === 0 ? styles.bestRow : undefined}>
                  <td>
                    {/* 色块与曲线图 overlay 颜色一一对应；仅前 4 条绘制曲线 */}
                    {i < 4 && (
                      <span
                        className={styles.swatch}
                        style={{ background: CURVE_COLORS[i % CURVE_COLORS.length] }}
                        aria-hidden
                      />
                    )}
                    {r.label}
                  </td>
                  <td className={styles.params}>{fmtParams(r.params)}</td>
                  <td>{r.r2.toFixed(4)}</td>
                  <td>{r.rmse.toFixed(4)}</td>
                  <td>{r.mae != null && Number.isFinite(r.mae) ? r.mae.toFixed(4) : '--'}</td>
                  <td>{r.aicc != null && Number.isFinite(r.aicc) ? r.aicc.toFixed(2) : '--'}</td>
                  <td>{r.n}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {fitResults[0] && (
            <div className={styles.bestHint}>
              最优：{fitResults[0].label}（R² = {fitResults[0].r2.toFixed(4)}）
              {/* 数据版本标注（T-03）：结果基于哪一份点集一目了然，防止与旧数据混淆 */}
              {` · 基于 ${fitPoints.length} 点`}
              {fitResults[0].x_min != null && fitResults[0].x_max != null
                ? ` · 有效区间 [${fitResults[0].x_min.toFixed(4)}, ${fitResults[0].x_max.toFixed(4)}]`
                : ''}
              {fitResults[0].extrapolation_forbidden !== false ? ' · 禁止外推' : ''}
              {fitResults[0].loocv_rmse != null && Number.isFinite(fitResults[0].loocv_rmse)
                ? ` · LOOCV RMSE ${fitResults[0].loocv_rmse.toFixed(4)}`
                : ''}
            </div>
          )}
          {fitResults.length > 4 && (
            <div className={styles.curveNote}>曲线图仅绘制 R² 最高的前 4 条</div>
          )}
        </div>
      )}

      {fitResults && fitResults.length > 0 && (
        <div className={styles.fitChart}>
          <StaticChart
            data={fitPoints}
            overlays={overlays}
            height={240}
            xLabel={AXIS_CHART_LABEL[xAxis]}
            dataStyle={xAxis === 'time' ? 'line' : 'scatter'}
          />
        </div>
      )}
    </div>
  );
}
