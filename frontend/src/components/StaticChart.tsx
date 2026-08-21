import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import styles from './StaticChart.module.css';

echarts.use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

export interface ChartOverlay {
  data: [number, number][];
  color?: string;
  name?: string;
}

interface Props {
  /** 静态数据点（历史复盘用，不做实时追加） */
  data: [number, number][];
  /** 叠加曲线（如拟合曲线），可多条 */
  overlays?: ChartOverlay[];
  height?: number;
  /** X 轴名称（默认「时间 (s)」，调用方可按物理含义覆盖） */
  xLabel?: string;
}

/** 静态曲线图：渲染数据点 + 可选叠加拟合/参考曲线。 */
export function StaticChart({ data, overlays, height = 260, xLabel = '时间 (s)' }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = echarts.init(el);
    chartRef.current = chart;
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption({
      animation: false,
      tooltip: { trigger: 'axis' },
      legend: { top: 0, left: 12, icon: 'roundRect', itemWidth: 14, itemHeight: 6 },
      grid: { left: 60, right: 20, top: 30, bottom: 40 },
      xAxis: {
        type: 'value',
        name: xLabel,
        nameLocation: 'middle',
        nameGap: 26,
        splitLine: { lineStyle: { type: 'dashed' } },
      },
      yAxis: {
        type: 'value',
        name: 'EC (μS/cm)',
        scale: true,
        splitLine: { lineStyle: { type: 'dashed' } },
      },
      series: [
        {
          name: '实测',
          type: 'line',
          data,
          symbol: 'none',
          lineStyle: { width: 1.5, color: '#2f6fed' },
          sampling: 'lttb',
        },
        ...(overlays ?? []).map((ov, i) => ({
          name: ov.name ?? `拟合 ${i + 1}`,
          type: 'line' as const,
          data: ov.data,
          symbol: 'none',
          lineStyle: { width: 2.5, color: ov.color ?? '#16a34a' },
        })),
      ],
    }, { notMerge: true });
  }, [data, overlays, xLabel]);

  return <div ref={ref} className={styles.chart} style={{ height }} />;
}
