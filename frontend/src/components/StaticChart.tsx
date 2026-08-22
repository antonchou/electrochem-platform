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
    // 容器尺寸变化不一定伴随 window resize（弹窗滚动条、提示条挤出布局等），
    // 仅监听 window 会让 canvas 被 CSS 拉伸而模糊，故用 ResizeObserver 兜底。
    const observer =
      typeof ResizeObserver !== 'undefined' ? new ResizeObserver(onResize) : null;
    observer?.observe(el);
    return () => {
      window.removeEventListener('resize', onResize);
      observer?.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption({
      animation: false,
      // 全局调色板：图例色块 / tooltip 圆点从这里取色。
      // 必须与各系列 lineStyle.color 一致，否则图例与线条颜色对不上。
      color: ['#2f6fed', ...(overlays ?? []).map((ov) => ov.color ?? '#16a34a')],
      tooltip: { trigger: 'axis' },
      // type:'scroll' 防止长公式名称（含 R²）单行溢出被裁；图标放大到 18×8 提升辨识度。
      legend: { top: 0, left: 12, type: 'scroll', icon: 'roundRect', itemWidth: 18, itemHeight: 8 },
      // top 需大于「图例条高度 + Y 轴名称高度」：Y 轴名称默认绘制在轴线顶端，
      // grid.top 太小会与顶部图例条重叠。
      grid: { left: 12, right: 20, top: 52, bottom: 40, containLabel: true },
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
          itemStyle: { color: '#2f6fed' },
          sampling: 'lttb',
        },
        ...(overlays ?? []).map((ov, i) => ({
          name: ov.name ?? `拟合 ${i + 1}`,
          type: 'line' as const,
          data: ov.data,
          symbol: 'none',
          lineStyle: { width: 2.5, color: ov.color ?? '#16a34a' },
          itemStyle: { color: ov.color ?? '#16a34a' },
        })),
      ],
    }, { notMerge: true });
  }, [data, overlays, xLabel]);

  return <div ref={ref} className={styles.chart} style={{ height }} />;
}
