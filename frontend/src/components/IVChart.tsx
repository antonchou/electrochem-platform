import { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart, ScatterChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { DataPoint } from '../types/protocol';
import { config } from '../config/config';
import { formatCurrentA, strideSample } from '../lib/units';
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
  return strideSample(pairs, DISPLAY_POINTS);
}

/**
 * 核心 I–V 图：原始点 + 仅在线性成立时画拟合直线。
 * 定时替换 series data，不重建 ECharts 实例。
 */
export function IVChart({ pointsRef, analysis, status }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const analysisRef = useRef(analysis);
  analysisRef.current = analysis;

  const iUnit = useMemo(() => {
    const maxAbs = Math.max(Math.abs(analysis.iMin ?? 0), Math.abs(analysis.iMax ?? 0));
    return formatCurrentA(maxAbs || 1e-6).unit;
  }, [analysis.iMin, analysis.iMax]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = echarts.init(el);
    chartRef.current = chart;
    chart.setOption({
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
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(onResize) : null;
    observer?.observe(el);
    return () => {
      window.removeEventListener('resize', onResize);
      observer?.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const scale = iUnit === 'mA' ? 1e3 : 1e6;
    const apply = () => {
      const fit = analysisRef.current;
      const scatter = scatterPairs(pointsRef.current, scale);
      const line =
        fit.linearOk && fit.fitLine
          ? fit.fitLine.map(([v, i]) => [v, i * scale] as [number, number])
          : [];
      chart.setOption({
        yAxis: { name: `电流 I (${iUnit})` },
        series: [{ data: scatter }, { data: line }],
      });
    };
    apply();
    if (status !== 'running') return;
    const id = window.setInterval(apply, config.chart.updateIntervalMs);
    return () => window.clearInterval(id);
  }, [pointsRef, status, iUnit, analysis.n, analysis.linearOk, analysis.fitLine]);

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
