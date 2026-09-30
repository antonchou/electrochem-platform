import { useEffect, useRef } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';
import type { Point } from '../lib/types.ts';

export type ChartOptions = Omit<uPlot.Options, 'width' | 'height'>;

interface ChartProps {
  /** 调用方用 useMemo 固定；变了才重建图表。 */
  options: ChartOptions;
  data: uPlot.AlignedData;
  height?: number;
  testId?: string;
}

/** uPlot 的薄封装：options 变了重建，data 变了只 setData，宽度跟随容器。 */
export function Chart({ options, data, height = 260, testId }: ChartProps) {
  const container = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const latestData = useRef(data);
  latestData.current = data;

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const instance = new uPlot({ ...options, width: element.clientWidth || 600, height }, latestData.current, element);
    plot.current = instance;
    const observer = new ResizeObserver(() => instance.setSize({ width: element.clientWidth, height }));
    observer.observe(element);
    return () => {
      observer.disconnect();
      instance.destroy();
      plot.current = null;
    };
  }, [options, height]);

  useEffect(() => {
    plot.current?.setData(data);
  }, [data]);

  return <div ref={container} className="chart" data-testid={testId} />;
}

const BLUE = '#2563eb';
const AMBER = '#d97706';
const GREY = '#64748b';

/** κ25（左轴）与温度（右轴）随时间。 */
export function kappaTimeOptions(xLabel = '时间 (s)'): ChartOptions {
  return {
    scales: { x: { time: false } },
    series: [
      { label: xLabel },
      { label: 'κ25 (µS/cm)', scale: 'k', stroke: BLUE, width: 2 },
      { label: 'T (°C)', scale: 'T', stroke: AMBER, width: 1.5 },
    ],
    axes: [
      { label: xLabel },
      { scale: 'k', label: 'κ25 (µS/cm)', size: 70 },
      { scale: 'T', side: 1, label: 'T (°C)', grid: { show: false } },
    ],
  };
}

export function kappaTimeData(points: Point[]): uPlot.AlignedData {
  return [
    points.map((p) => p.t_s),
    points.map((p) => p.kappa25_us_cm),
    points.map((p) => p.temperature_c),
  ];
}

/** 原始 U / I 随时间（诊断用）。 */
export function rawTimeOptions(): ChartOptions {
  return {
    scales: { x: { time: false } },
    series: [
      { label: '时间 (s)' },
      { label: 'U (mV)', scale: 'U', stroke: GREY, width: 1.5 },
      { label: 'I (µA)', scale: 'I', stroke: BLUE, width: 1.5 },
    ],
    axes: [
      { label: '时间 (s)' },
      { scale: 'U', label: 'U (mV)', size: 60 },
      { scale: 'I', side: 1, label: 'I (µA)', grid: { show: false } },
    ],
  };
}

export function rawTimeData(points: Point[]): uPlot.AlignedData {
  return [
    points.map((p) => p.t_s),
    points.map((p) => (p.voltage_v === null ? null : p.voltage_v * 1e3)),
    points.map((p) => (p.current_a === null ? null : p.current_a * 1e6)),
  ];
}
