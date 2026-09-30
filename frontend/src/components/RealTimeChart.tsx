import { useEffect, useRef } from 'react';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { DataPoint } from '../types/protocol';
import { config } from '../config/config';
import { useEChart } from '../hooks/useEChart';
import { mergeAxisBounds, paddedBounds, type AxisBounds } from '../lib/axis';
import styles from './RealTimeChart.module.css';

// 按需注册，避免全量打包（对树莓派端加载与渲染更友好）
echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

/** 电导率轴：初始窗口至少 10 μS/cm 或约为读数的 1%，有真实波动时额外留 20%；下界不低于 0。 */
function paddedYAxisBounds(dataMin: number, dataMax: number): AxisBounds {
  const bounds = paddedBounds(dataMin, dataMax, 10, 0.01);
  return { min: Math.max(0, bounds.min), max: bounds.max };
}

/** 全量重建的滞回余量：序列超过 上限×1.2 才触发，见定时器内注释。 */
const REBUILD_HEADROOM = 1.2;

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
  const chartRef = useEChart(containerRef);

  useEffect(() => {
    const el = containerRef.current;
    const chart = chartRef.current;
    if (!el || !chart) return;

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
        // 同一轮实验内坐标只扩展、不收缩，消除实时读数造成的刻度抖动。
        yBounds = mergeAxisBounds(yBounds, paddedYAxisBounds(minEcSeen, maxEcSeen));
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

      // lastIndexOf 从尾部反向找：封顶后 lastRenderedPoint 紧贴缓冲尾部，
      // 前向 indexOf 会退化为每 tick O(n) 全扫。
      const lastIndex = lastRenderedPoint ? pts.lastIndexOf(lastRenderedPoint) : -1;
      const newPoints = lastIndex >= 0 ? pts.slice(lastIndex + 1) : pts;
      // 滞回重建：序列超出上限一定余量才全量重建，否则 appendData 增量。
      // 缓冲封顶后 renderedCount 恒等于上限，若按“超出即重建”判定，
      // 每个 tick 都会触发 2 万点全量 setOption（B2）。余量取上限的 20%，
      // 即约每 4000 点（10Hz 下 ~6.7 分钟）重建一次。
      const rebuildLimit = Math.ceil(config.chart.maxPoints * REBUILD_HEADROOM);
      if (lastIndex < 0 || renderedCount + newPoints.length > rebuildLimit) {
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
    return () => window.clearInterval(timer);
  }, [chartRef, pointsRef]);

  return <div ref={containerRef} className={styles.chart} data-testid="ec-t-chart" />;
}
