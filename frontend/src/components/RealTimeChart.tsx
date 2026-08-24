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

interface AxisBounds {
  min: number;
  max: number;
}

/** 为电导率选择易读的刻度步长，避免首批少量数据导致坐标范围过窄或过宽。 */
function niceStep(span: number): number {
  const roughStep = Math.max(span / 6, Number.EPSILON);
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const normalized = roughStep / magnitude;
  const multiplier = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return multiplier * magnitude;
}

function paddedYAxisBounds(dataMin: number, dataMax: number): AxisBounds {
  const center = (dataMin + dataMax) / 2;
  // 初始窗口至少 10 μS/cm，或约为读数的 1%；有真实波动时额外留 20% 空间。
  const span = Math.max((dataMax - dataMin) * 1.2, Math.abs(center) * 0.01, 10);
  const step = niceStep(span);
  return {
    min: Math.max(0, Math.floor((center - span / 2) / step) * step),
    max: Math.ceil((center + span / 2) / step) * step,
  };
}

interface Props {
  /** 数据缓冲（由 useRealtimeData 提供，ref 保证读到最新） */
  pointsRef: React.MutableRefObject<DataPoint[]>;
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
        min: 0,
        max: 1,
        minInterval: 1,
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

    let observedBuffer = pointsRef.current;
    let lastRenderedPoint: DataPoint | null = null;
    let renderedCount = 0;
    let maxTimeSeen = 0;
    let minEcSeen = Number.POSITIVE_INFINITY;
    let maxEcSeen = Number.NEGATIVE_INFINITY;
    let yBounds: AxisBounds | null = null;

    const resetAxisTracking = () => {
      lastRenderedPoint = null;
      renderedCount = 0;
      maxTimeSeen = 0;
      minEcSeen = Number.POSITIVE_INFINITY;
      maxEcSeen = Number.NEGATIVE_INFINITY;
      yBounds = null;
    };

    const includeInAxes = (points: DataPoint[]) => {
      for (const point of points) {
        maxTimeSeen = Math.max(maxTimeSeen, point.t);
        if (point.ec === null || !Number.isFinite(point.ec)) continue;
        minEcSeen = Math.min(minEcSeen, point.ec);
        maxEcSeen = Math.max(maxEcSeen, point.ec);
      }
      if (Number.isFinite(minEcSeen) && Number.isFinite(maxEcSeen)) {
        const candidate = paddedYAxisBounds(minEcSeen, maxEcSeen);
        // 同一轮实验内坐标只扩展、不收缩，消除实时读数造成的刻度抖动。
        yBounds = yBounds
          ? { min: Math.min(yBounds.min, candidate.min), max: Math.max(yBounds.max, candidate.max) }
          : candidate;
      }
    };

    const axisOption = () => ({
      xAxis: { min: 0, max: Math.max(1, Math.ceil(maxTimeSeen)) },
      yAxis: yBounds
        ? { min: yBounds.min, max: yBounds.max }
        : { min: 'dataMin', max: 'dataMax' },
    });

    const publishAxisDiagnostics = () => {
      el.dataset.chartXMin = '0';
      el.dataset.chartXMax = String(Math.max(1, Math.ceil(maxTimeSeen)));
      if (yBounds) {
        el.dataset.chartYMin = String(yBounds.min);
        el.dataset.chartYMax = String(yBounds.max);
      } else {
        delete el.dataset.chartYMin;
        delete el.dataset.chartYMax;
      }
    };

    const replaceAll = (points: DataPoint[], resetAxes = false) => {
      if (resetAxes) resetAxisTracking();
      includeInAxes(points);
      const data = points
        .filter((point) => point.ec !== null && Number.isFinite(point.ec))
        .map((point) => [point.t, point.ec as number] as [number, number]);
      chart.setOption({ ...axisOption(), series: [{ data }] });
      renderedCount = points.length;
      lastRenderedPoint = points.length > 0 ? points[points.length - 1] : null;
      publishAxisDiagnostics();
    };

    const timer = window.setInterval(() => {
      const pts = pointsRef.current;
      // start/reset 会替换缓冲数组；新实验必须重新建立坐标范围。
      if (pts !== observedBuffer) {
        observedBuffer = pts;
        replaceAll(pts, true);
        return;
      }
      if (pts.length === 0) {
        if (renderedCount > 0) replaceAll(pts, true);
        return;
      }
      if (lastRenderedPoint === pts[pts.length - 1]) return;

      const lastIndex = lastRenderedPoint ? pts.indexOf(lastRenderedPoint) : -1;
      const newPoints = lastIndex >= 0 ? pts.slice(lastIndex + 1) : pts;
      if (lastIndex < 0 || renderedCount + newPoints.length > config.chart.maxPoints) {
        replaceAll(pts);
        return;
      }
      if (newPoints.length > 0) {
        includeInAxes(newPoints);
        chart.appendData({
          seriesIndex: 0,
          data: newPoints
            .filter((point) => point.ec !== null && Number.isFinite(point.ec))
            .map((point) => [point.t, point.ec as number]),
        });
        // appendData 不会可靠地重算 value 轴范围，必须显式同步坐标轴。
        chart.setOption(axisOption());
        renderedCount += newPoints.length;
        lastRenderedPoint = newPoints[newPoints.length - 1] ?? lastRenderedPoint;
        publishAxisDiagnostics();
      }
    }, config.chart.updateIntervalMs);

    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    // 与 StaticChart 一致：容器尺寸变化（布局挤出等）不一定触发 window resize，
    // 用 ResizeObserver 兜底，避免 canvas 被 CSS 拉伸变模糊。
    const observer =
      typeof ResizeObserver !== 'undefined' ? new ResizeObserver(onResize) : null;
    observer?.observe(el);

    return () => {
      window.clearInterval(timer);
      window.removeEventListener('resize', onResize);
      observer?.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [pointsRef]);

  return <div ref={containerRef} className={styles.chart} data-testid="realtime-chart" />;
}
