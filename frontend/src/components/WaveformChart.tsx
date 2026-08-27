import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { DataPoint } from '../types/protocol';
import { config } from '../config/config';
import { strideSample } from '../lib/units';
import styles from './WaveformChart.module.css';

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

interface AxisBounds {
  min: number;
  max: number;
}

function niceStep(span: number): number {
  const roughStep = Math.max(span / 6, Number.EPSILON);
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const normalized = roughStep / magnitude;
  const multiplier = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return multiplier * magnitude;
}

function paddedBounds(dataMin: number, dataMax: number, minSpan: number): AxisBounds {
  const center = (dataMin + dataMax) / 2;
  const span = Math.max((dataMax - dataMin) * 1.2, Math.abs(center) * 0.02, minSpan);
  const step = niceStep(span);
  return {
    min: Math.floor((center - span / 2) / step) * step,
    max: Math.ceil((center + span / 2) / step) * step,
  };
}

interface Props {
  pointsRef: React.MutableRefObject<DataPoint[]>;
}

const DISPLAY_POINTS = 4000;

/**
 * 实时 V(t)/I(t)。定时读取缓冲并整表 setOption，不重建 ECharts 实例。
 */
export function WaveformChart({ pointsRef }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = echarts.init(el);
    chart.setOption({
      animation: false,
      tooltip: { trigger: 'axis' },
      legend: { top: 0, left: 8, icon: 'roundRect', itemWidth: 16, itemHeight: 8 },
      grid: { left: 58, right: 58, top: 36, bottom: 44 },
      xAxis: {
        type: 'value',
        min: 0,
        max: 1,
        name: '时间 (s)',
        nameLocation: 'middle',
        nameGap: 28,
        splitLine: { lineStyle: { type: 'dashed' } },
      },
      yAxis: [
        {
          type: 'value',
          name: '电压 V (V)',
          scale: true,
          splitLine: { lineStyle: { type: 'dashed' } },
        },
        {
          type: 'value',
          name: '电流 I (μA)',
          scale: true,
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: 'V(t)',
          type: 'line',
          yAxisIndex: 0,
          data: [],
          symbol: 'none',
          lineStyle: { width: 1.5, color: '#2f6fed' },
          itemStyle: { color: '#2f6fed' },
          sampling: 'lttb',
        },
        {
          name: 'I(t)',
          type: 'line',
          yAxisIndex: 1,
          data: [],
          symbol: 'none',
          lineStyle: { width: 1.5, color: '#d97706' },
          itemStyle: { color: '#d97706' },
          sampling: 'lttb',
        },
      ],
    });

    let observedBuffer: DataPoint[] | null = null;
    let vBounds: AxisBounds | null = null;
    let iBounds: AxisBounds | null = null;

    const render = () => {
      const source = pointsRef.current;
      if (source !== observedBuffer) {
        observedBuffer = source;
        vBounds = null;
        iBounds = null;
      }
      const pts = strideSample(source, DISPLAY_POINTS);

      const vData: [number, number][] = [];
      const iData: [number, number][] = [];
      let maxT = 0;
      let minV = Number.POSITIVE_INFINITY;
      let maxV = Number.NEGATIVE_INFINITY;
      let minI = Number.POSITIVE_INFINITY;
      let maxI = Number.NEGATIVE_INFINITY;
      for (const point of pts) {
        maxT = Math.max(maxT, point.t);
        if (point.voltage_raw_v != null && Number.isFinite(point.voltage_raw_v)) {
          vData.push([point.t, point.voltage_raw_v]);
          minV = Math.min(minV, point.voltage_raw_v);
          maxV = Math.max(maxV, point.voltage_raw_v);
        }
        if (point.current_raw_a != null && Number.isFinite(point.current_raw_a)) {
          iData.push([point.t, point.current_raw_a]);
          minI = Math.min(minI, point.current_raw_a);
          maxI = Math.max(maxI, point.current_raw_a);
        }
      }

      const iUnit: 'μA' | 'mA' =
        Number.isFinite(minI) && Math.max(Math.abs(minI), Math.abs(maxI)) >= 1e-3 ? 'mA' : 'μA';
      const iScale = iUnit === 'mA' ? 1e3 : 1e6;
      const iPlot = iData.map(([t, i]) => [t, i * iScale] as [number, number]);

      if (Number.isFinite(minV) && Number.isFinite(maxV)) {
        const next = paddedBounds(minV, maxV, 0.05);
        vBounds = vBounds
          ? { min: Math.min(vBounds.min, next.min), max: Math.max(vBounds.max, next.max) }
          : next;
      }
      if (Number.isFinite(minI) && Number.isFinite(maxI)) {
        const next = paddedBounds(minI * iScale, maxI * iScale, iUnit === 'mA' ? 0.05 : 5);
        iBounds = iBounds
          ? { min: Math.min(iBounds.min, next.min), max: Math.max(iBounds.max, next.max) }
          : next;
      }

      try {
        chart.setOption({
          xAxis: { min: 0, max: Math.max(1, Math.ceil(maxT)) },
          yAxis: [
            {
              name: '电压 V (V)',
              ...(vBounds ? { min: vBounds.min, max: vBounds.max } : {}),
            },
            {
              name: `电流 I (${iUnit})`,
              ...(iBounds ? { min: iBounds.min, max: iBounds.max } : {}),
            },
          ],
          series: [
            { type: 'line', yAxisIndex: 0, data: vData },
            { type: 'line', yAxisIndex: 1, data: iPlot },
          ],
        });
      } finally {
        el.dataset.chartXMin = '0';
        el.dataset.chartXMax = String(Math.max(1, Math.ceil(maxT)));
        if (vBounds) {
          el.dataset.chartVMin = String(vBounds.min);
          el.dataset.chartVMax = String(vBounds.max);
        } else {
          delete el.dataset.chartVMin;
          delete el.dataset.chartVMax;
        }
        if (iBounds) {
          el.dataset.chartIMin = String(iBounds.min);
          el.dataset.chartIMax = String(iBounds.max);
        } else {
          delete el.dataset.chartIMin;
          delete el.dataset.chartIMax;
        }
      }
    };

    render();
    const timer = window.setInterval(render, config.chart.updateIntervalMs);
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(onResize) : null;
    observer?.observe(el);

    return () => {
      window.clearInterval(timer);
      window.removeEventListener('resize', onResize);
      observer?.disconnect();
      chart.dispose();
    };
  }, [pointsRef]);

  return <div ref={containerRef} className={styles.chart} data-testid="realtime-chart" />;
}
