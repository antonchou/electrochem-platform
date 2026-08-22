import { useMemo, useRef, useState } from 'react';
import type { DataPoint, FitAxis, FitResultItem } from '../types/protocol';
import type { ApiClient } from '../services/apiClient';
import { StaticChart, type ChartOverlay } from './StaticChart';
import styles from './FitPanel.module.css';

interface Props {
  api: ApiClient | null;
  /** 数据点（t 秒 / tc °C / ec μS·cm⁻¹），按 X 轴语义自动取 x */
  points: DataPoint[];
  /** data-testid 前缀，供多处使用互不冲突 */
  testIdPrefix?: string;
  /** 开始拟合按钮的 testid（ResultPanel 保持历史值 btn-fit） */
  btnTestId?: string;
}

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
export function FitPanel({ api, points, testIdPrefix = 'fit', btnTestId }: Props) {
  const [xAxis, setXAxis] = useState<FitAxis>('time');
  const [selectedModels, setSelectedModels] = useState<string[]>(
    AXIS_MODELS.time.map((m) => m.key),
  );
  const [fitResults, setFitResults] = useState<FitResultItem[] | null>(null);
  const [fitLoading, setFitLoading] = useState(false);
  const [fitError, setFitError] = useState<string | null>(null);
  const fitRequestIdRef = useRef(0);

  // 按 X 轴语义构造 (x, y)。y 明确使用电导率层级：κ25 优先（Derived），
  // 未校准时回退 κ(T)（Calibrated）；绝不使用原始 U/I/T 做电导率拟合。
  // 时间轴取 t，温度轴取帧内温度 tc；浓度轴取 p.concentration，缺省用序号 1..N 占位
  const fitPoints: [number, number][] = useMemo(
    () =>
      points
        .map((p, i) => {
          const y = p.k25 ?? p.kt;
          if (!Number.isFinite(y)) return null;
          if (xAxis === 'temperature') return [p.tc, y] as [number, number];
          if (xAxis === 'concentration')
            return [p.concentration ?? i + 1, y] as [number, number];
          return [p.t, y] as [number, number];
        })
        .filter((v): v is [number, number] => v !== null),
    [points, xAxis],
  );
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
    const requestId = ++fitRequestIdRef.current;
    setFitLoading(true);
    setFitError(null);
    try {
      const res = await api.fitPoints(fitPoints, selectedModels, xAxis);
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

  const canFit = api !== null && fitPoints.length >= 3 && selectedModels.length > 0 && !fitLoading;

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
        {(Object.keys(AXIS_MODELS) as FitAxis[]).map((axis) => (
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

      {xAxis === 'concentration' && (
        <div className={styles.axisNote} data-testid={`${testIdPrefix}-concentration-note`}>
          浓度轴需真实浓度数据：当前数据帧无浓度字段，x 暂用序号 1..N 占位
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
          所选模型均未产生有效拟合结果：数据点不足或不满足模型约束（如对数/幂函数要求 x&gt;0）。
        </div>
      )}

      {fitResults && fitResults.length > 0 && (
        <div className={styles.fitResults} data-testid={`${testIdPrefix}-results`}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>公式</th>
                <th>参数</th>
                <th>R²</th>
                <th>RMSE</th>
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
                  <td>{r.n}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {fitResults[0] && (
            <div className={styles.bestHint}>
              最优：{fitResults[0].label}（R² = {fitResults[0].r2.toFixed(4)}）
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
          />
        </div>
      )}
    </div>
  );
}
