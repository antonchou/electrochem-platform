import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { DataPoint } from '../types/protocol';
import { config } from '../config/config';
import styles from './RealTimeChart.module.css';

// 按需注册，避免全量打包（对树莓派端加载与渲染更友好）
echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

interface Props {
  /** 数据缓冲（由 useRealtimeData 提供，ref 保证读到最新） */
  pointsRef: React.MutableRefObject<DataPoint[]>;
}

const COLORS = {
  kt: '#2f6fed',
  k25: '#16a34a',
  u: '#d97706',
  i: '#7c3aed',
  tc: '#0891b2',
};

function mainYAxes(bounds: { min?: number; max?: number } = {}) {
  return [
    {
      type: 'value' as const,
      name: 'κ(T) / κ25 (μS/cm)',
      scale: true,
      splitLine: { lineStyle: { type: 'dashed' as const } },
      ...bounds,
    },
    {
      type: 'value' as const,
      name: 'U (V) / I (mA)',
      scale: true,
      splitLine: { show: false },
    },
  ];
}

function xAxisMax(points: DataPoint[]): number {
  const maxObservedTime = points.reduce(
    (max, point) => (Number.isFinite(point.t) ? Math.max(max, point.t) : max),
    0,
  );
  return Math.max(1, Math.ceil(maxObservedTime / 10) * 10);
}

/**
 * 实时曲线（分层显示，SRS v0.2）：
 * - 主图：主 Y 轴 κ(T)（实线）、κ25（虚线）；副 Y 轴 U（V）、I（mA）。
 *   电压/电流与电导率数量级不同，绝不混画同一轴。
 * - 温度独立小图：T（°C）。
 * X 轴均为实验经过时间（s）。
 * 未校准时 κ25 为 null → 曲线断点，不伪造数据。
 */
export function RealTimeChart({ pointsRef }: Props) {
  const mainRef = useRef<HTMLDivElement>(null);
  const tempRef = useRef<HTMLDivElement>(null);

  // ---- 主图：κ(T)/κ25 + U/I 双轴 ----
  useEffect(() => {
    const el = mainRef.current;
    if (!el) return;
    const chart = echarts.init(el);
    let observedYMin: number | null = null;
    let observedYMax: number | null = null;
    let activeBuffer = pointsRef.current;
    chart.setOption({
      animation: false,
      tooltip: { trigger: 'axis' },
      grid: { left: 68, right: 72, top: 44, bottom: 46 },
      legend: { top: 4, data: ['κ(T)', 'κ25', 'U', 'I'] },
      xAxis: { type: 'value', min: 0, max: 1, minInterval: 1, name: '时间 (s)', nameLocation: 'middle', nameGap: 30, splitLine: { lineStyle: { type: 'dashed' } } },
      yAxis: mainYAxes(),
      series: [
        { name: 'κ(T)', type: 'line', yAxisIndex: 0, data: [], symbol: 'none', lineStyle: { width: 1.5, color: COLORS.kt }, sampling: 'lttb' },
        { name: 'κ25', type: 'line', yAxisIndex: 0, data: [], symbol: 'none', lineStyle: { width: 1.5, color: COLORS.k25, type: 'dashed' }, sampling: 'lttb' },
        { name: 'U', type: 'line', yAxisIndex: 1, data: [], symbol: 'none', lineStyle: { width: 1, color: COLORS.u }, sampling: 'lttb' },
        { name: 'I', type: 'line', yAxisIndex: 1, data: [], symbol: 'none', lineStyle: { width: 1, color: COLORS.i }, sampling: 'lttb' },
      ],
    });

    const timer = window.setInterval(() => {
      const pts = pointsRef.current;
      if (pts !== activeBuffer) {
        activeBuffer = pts;
        observedYMin = null;
        observedYMax = null;
      }
      const data = pts.map((p) => [
        [p.t, p.kt !== null && Number.isFinite(p.kt) ? p.kt : null],
        [p.t, p.k25 !== null && Number.isFinite(p.k25) ? p.k25 : null],
        [p.t, p.u ?? null],
        [p.t, p.i ?? null],
      ] as const);
      // burst 与真实采集属于不同 trace，帧到达顺序不保证 t_seconds 全局递增；
      // 取缓冲内最大时间，避免后到的真实帧把调试后的 X 轴缩回去。
      const maxTime = xAxisMax(pts);
      const conductivityValues = pts.flatMap((p) =>
        [p.kt, p.k25].filter((value): value is number => value !== null && Number.isFinite(value)),
      );
      let yAxisBounds: { min?: number; max?: number } = {};
      if (conductivityValues.length === 0) {
        observedYMin = null;
        observedYMax = null;
        delete el.dataset.chartYMin;
        delete el.dataset.chartYMax;
      } else {
        const currentMin = Math.min(...conductivityValues);
        const currentMax = Math.max(...conductivityValues);
        observedYMin = observedYMin === null ? currentMin : Math.min(observedYMin, currentMin);
        observedYMax = observedYMax === null ? currentMax : Math.max(observedYMax, currentMax);
        const span = observedYMax - observedYMin;
        const padding = Math.max(span * 0.05, Math.abs(observedYMax) * 0.002, 0.1);
        yAxisBounds = { min: observedYMin - padding, max: observedYMax + padding };
        el.dataset.chartYMin = String(yAxisBounds.min);
        el.dataset.chartYMax = String(yAxisBounds.max);
      }
      el.dataset.chartXMax = String(maxTime);
      chart.setOption(
        {
          xAxis: { min: 0, max: maxTime },
          // replaceMerge 会真正移除上一轮的显式 min/max；普通 `{}` merge 会保留旧范围。
          yAxis: mainYAxes(yAxisBounds),
          series: [
            { data: data.map((d) => d[0]) },
            { data: data.map((d) => d[1]) },
            { data: data.map((d) => d[2]) },
            { data: data.map((d) => d[3]) },
          ],
        },
        { replaceMerge: ['yAxis'] },
      );
    }, config.chart.updateIntervalMs);

    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    const observer =
      typeof ResizeObserver !== 'undefined' ? new ResizeObserver(onResize) : null;
    observer?.observe(el);

    return () => {
      window.clearInterval(timer);
      window.removeEventListener('resize', onResize);
      observer?.disconnect();
      chart.dispose();
    };
  }, [pointsRef]);

  // ---- 温度独立小图：T (°C) ----
  useEffect(() => {
    const el = tempRef.current;
    if (!el) return;
    const chart = echarts.init(el);
    chart.setOption({
      animation: false,
      tooltip: { trigger: 'axis' },
      grid: { left: 62, right: 24, top: 24, bottom: 32 },
      xAxis: { type: 'value', min: 0, max: 1, minInterval: 1, name: '时间 (s)', nameLocation: 'middle', nameGap: 24, splitLine: { lineStyle: { type: 'dashed' } } },
      yAxis: { type: 'value', name: 'T (°C)', scale: true, splitLine: { lineStyle: { type: 'dashed' } } },
      series: [{ name: 'T', type: 'line', data: [], symbol: 'none', lineStyle: { width: 1.2, color: COLORS.tc }, sampling: 'lttb' }],
    });

    const timer = window.setInterval(() => {
      const pts = pointsRef.current;
      const maxTime = xAxisMax(pts);
      chart.setOption({
        xAxis: { min: 0, max: maxTime },
        series: [
          {
            data: pts.map(
              (p) =>
                [p.t, p.tc !== null && Number.isFinite(p.tc) ? p.tc : null] as [
                  number,
                  number | null,
                ],
            ),
          },
        ],
      });
    }, config.chart.updateIntervalMs);

    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    const observer =
      typeof ResizeObserver !== 'undefined' ? new ResizeObserver(onResize) : null;
    observer?.observe(el);

    return () => {
      window.clearInterval(timer);
      window.removeEventListener('resize', onResize);
      observer?.disconnect();
      chart.dispose();
    };
  }, [pointsRef]);

  return (
    <div className={styles.wrap}>
      <div ref={mainRef} className={styles.chart} data-testid="realtime-chart" />
      <div className={styles.tempHead}>温度曲线 · T (°C)</div>
      <div ref={tempRef} className={styles.tempChart} data-testid="realtime-temp-chart" />
    </div>
  );
}
