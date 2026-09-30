import { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart, ScatterChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { DataPoint } from '../types/protocol';
import { config } from '../config/config';
import { useEChart } from '../hooks/useEChart';
import { downsample } from '../lib/downsample';
import { formatCurrentA } from '../lib/units';
import { formatIVEquation, ivReasonMessage, type IVAnalysis } from '../lib/ivAnalysis';
import styles from './IVChart.module.css';

echarts.use([ScatterChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

const DISPLAY_POINTS = 1500;

interface Props {
  pointsRef: React.MutableRefObject<DataPoint[]>;
  analysis: IVAnalysis;
  status: string;
}

function scatterPairs(points: DataPoint[], scale: number): [number, number][] {
  const pairs: [number, number][] = [];
  for (const p of points) {
    if (p.voltage_raw_v == null || p.current_raw_a == null) continue;
    if (!Number.isFinite(p.voltage_raw_v) || !Number.isFinite(p.current_raw_a)) continue;
    pairs.push([p.voltage_raw_v, p.current_raw_a * scale]);
  }
  return downsample(pairs, DISPLAY_POINTS);
}

/** 把实验点与（仅线性成立时的）拟合直线按当前电流单位画上去。 */
function draw(chart: echarts.ECharts, points: DataPoint[], fit: IVAnalysis, iUnit: 'μA' | 'mA') {
  const scale = iUnit === 'mA' ? 1e3 : 1e6;
  const line =
    fit.linearOk && fit.fitLine ? fit.fitLine.map(([v, i]) => [v, i * scale] as [number, number]) : [];
  chart.setOption({
    yAxis: { name: `电流 I (${iUnit})` },
    series: [{ data: scatterPairs(points, scale) }, { data: line }],
  });
}

/**
 * 核心 I–V 图：原始点 + 仅在线性成立时画拟合直线。
 * 定时替换 series data，不重建 ECharts 实例。
 */
export function IVChart({ pointsRef, analysis, status }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useEChart(containerRef);
  const analysisRef = useRef(analysis);
  analysisRef.current = analysis;

  const iUnit = useMemo(() => {
    const maxAbs = Math.max(Math.abs(analysis.iMin ?? 0), Math.abs(analysis.iMax ?? 0));
    return formatCurrentA(maxAbs || 1e-6).unit;
  }, [analysis.iMin, analysis.iMax]);

  useEffect(() => {
    chartRef.current?.setOption({
      animation: false,
      color: ['#2f6fed', '#dc2626'],
      tooltip: { trigger: 'item' },
      legend: { top: 0, left: 8, icon: 'roundRect', itemWidth: 16, itemHeight: 8 },
      grid: { left: 56, right: 24, top: 36, bottom: 44 },
      xAxis: {
        type: 'value',
        name: '电压 V (V)',
        nameLocation: 'middle',
        nameGap: 28,
        scale: true,
        splitLine: { lineStyle: { type: 'dashed' } },
      },
      yAxis: {
        type: 'value',
        name: '电流 I (μA)',
        scale: true,
        splitLine: { lineStyle: { type: 'dashed' } },
      },
      series: [
        {
          name: '实验点',
          type: 'scatter',
          data: [],
          symbolSize: 7,
          itemStyle: { color: '#2f6fed', opacity: 0.7 },
        },
        {
          name: '线性拟合',
          type: 'line',
          data: [],
          symbol: 'none',
          lineStyle: { width: 2.5, color: '#dc2626' },
          itemStyle: { color: '#dc2626' },
        },
      ],
    });
  }, [chartRef]);

  // 运行中：固定节流间隔重绘（analysis 经 ref 读取，避免每帧变化的重算依赖
  // 把 effect 打成 10Hz 同步重绘、节流失效）；非运行中：analysis 变化时补画一次
  // （覆盖停止后统计更新、续跑历史水合等场景）。
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || status !== 'running') return;
    const apply = () => draw(chart, pointsRef.current, analysisRef.current, iUnit);
    apply();
    const id = window.setInterval(apply, config.chart.updateIntervalMs);
    return () => window.clearInterval(id);
  }, [chartRef, pointsRef, status, iUnit]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || status === 'running') return;
    draw(chart, pointsRef.current, analysisRef.current, iUnit);
  }, [chartRef, pointsRef, status, iUnit, analysis]);

  const equation =
    analysis.linearOk && analysis.slopeS != null && analysis.interceptA != null
      ? formatIVEquation(analysis.slopeS, analysis.interceptA)
      : null;

  return (
    <div className={styles.wrap}>
      <div className={styles.meta}>
        {equation ? (
          <span className={styles.equation} data-testid="iv-equation">
            {equation}
          </span>
        ) : (
          <span className={styles.equation} data-testid="iv-equation">
            暂无线性方程
          </span>
        )}
        <span className={styles.r2} data-testid="iv-r2">
          {analysis.linearOk && analysis.r2 != null ? `R² = ${analysis.r2.toFixed(3)}` : 'R² = --'}
        </span>
      </div>
      <p
        className={`${styles.note} ${analysis.linearOk ? styles.ok : styles.warn}`}
        data-testid="iv-note"
      >
        {ivReasonMessage(analysis.reason)}
      </p>
      <div ref={containerRef} className={styles.chart} data-testid="iv-chart" />
    </div>
  );
}
