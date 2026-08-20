import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, DataZoomComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { DataPoint } from '../types/protocol';
import { config } from '../config/config';
import styles from './RealTimeChart.module.css';

// 按需注册，避免全量打包（对树莓派端加载与渲染更友好）
echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, CanvasRenderer]);

interface Props {
  /** 数据缓冲（由 useRealtimeData 提供，ref 保证读到最新） */
  pointsRef: React.MutableRefObject<DataPoint[]>;
  /** 是否运行中（用于在停止后维持曲线显示） */
  running: boolean;
}

/**
 * ECharts 实时曲线：以固定节流间隔读取数据缓冲并增量更新。
 * - 数据到达不刷新页面（WebSocket 推送 → 内存 → 定时 setOption）
 * - 停止后保留曲线
 * - 采样开启 lttb，单次承载 ≥1 万点不卡（P04）
 */
export function RealTimeChart({ pointsRef }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = echarts.init(el);
    chartRef.current = chart;

    chart.setOption({
      animation: false,
      tooltip: { trigger: 'axis' },
      grid: { left: 68, right: 28, top: 40, bottom: 48 },
      xAxis: {
        type: 'value',
        name: '时间 (s)',
        nameLocation: 'middle',
        nameGap: 30,
        splitLine: { lineStyle: { type: 'dashed' } },
      },
      yAxis: {
        type: 'value',
        name: '电导率 EC (μS/cm)',
        scale: true,
        splitLine: { lineStyle: { type: 'dashed' } },
      },
      dataZoom: [{ type: 'inside', xAxisIndex: 0, filterMode: 'none' }],
      series: [
        {
          name: 'EC',
          type: 'line',
          data: [],
          symbol: 'none',
          lineStyle: { width: 1.5, color: '#2f6fed' },
          itemStyle: { color: '#2f6fed' },
          sampling: 'lttb',
        },
      ],
    });

    const timer = window.setInterval(() => {
      const pts = pointsRef.current;
      const data: [number, number][] = new Array(pts.length);
      for (let i = 0; i < pts.length; i++) {
        data[i] = [pts[i].t, pts[i].ec];
      }
      chartRef.current?.setOption({ series: [{ data }] });
    }, config.chart.updateIntervalMs);

    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);

    return () => {
      window.clearInterval(timer);
      window.removeEventListener('resize', onResize);
      chart.dispose();
      chartRef.current = null;
    };
  }, [pointsRef]);

  return <div ref={containerRef} className={styles.chart} data-testid="realtime-chart" />;
}
