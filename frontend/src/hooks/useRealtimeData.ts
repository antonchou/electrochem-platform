import { useCallback, useEffect, useRef, useState } from 'react';
import type { DataPoint, RawFrame } from '../types/protocol';
import { config } from '../config/config';
import type { ExperimentBridge } from '../services';

function rawFrameToPoint(frame: RawFrame): DataPoint {
  return {
    t: frame.t_seconds ?? 0,
    ec: frame.kappa_25_us_cm ?? frame.k25 ?? frame.ec_raw,
    tc: frame.temperature_raw,
    voltage_raw_v: frame.voltage_raw_v ?? undefined,
    current_raw_a: frame.current_raw_a ?? undefined,
    conductance_s: frame.conductance_s ?? undefined,
    kappa_t_us_cm: frame.kappa_t_us_cm ?? undefined,
    kappa_25_us_cm: frame.kappa_25_us_cm ?? undefined,
    quality_flags: frame.quality_flags ?? undefined,
  };
}

/**
 * 实时数据缓冲。
 * 数据点保存在 ref（可变数组）中，避免每次帧都触发大规模 re-render；
 * 通过 count/latest 的 setState（≈10Hz）驱动 UI 增量更新；
 * 曲线组件以固定间隔直接读 pointsRef 绘制。
 */
export function useRealtimeData(bridge: ExperimentBridge) {
  const pointsRef = useRef<DataPoint[]>([]);
  const [count, setCount] = useState(0);
  const [latest, setLatest] = useState<DataPoint | null>(null);
  const runStartTRef = useRef<number | null>(null);

  const clearPoints = useCallback(() => {
    pointsRef.current = [];
    runStartTRef.current = null;
    setCount(0);
    setLatest(null);
  }, []);

  const hydrateFromFrames = useCallback((frames: RawFrame[]) => {
    const converted = frames.map(rawFrameToPoint);
    const lastT = converted.length > 0 ? converted[converted.length - 1].t : Number.NEGATIVE_INFINITY;
    const extra = pointsRef.current.filter((p) => p.t > lastT);
    const merged = converted.concat(extra);
    pointsRef.current = merged;
    runStartTRef.current = merged.length > 0 ? merged[0].t : null;
    setCount(merged.length);
    setLatest(merged.length > 0 ? merged[merged.length - 1] : null);
  }, []);

  useEffect(() => {
    const unsub = bridge.subscribe((ev) => {
      if (ev.type === 'message') {
        const { frame } = ev;
        const p: DataPoint = {
          t: frame.timestamp,
          ec: frame.ec ?? frame.kappa_25_us_cm ?? null,
          tc: frame.temperature,
          voltage_raw_v: frame.voltage_raw_v,
          current_raw_a: frame.current_raw_a,
          conductance_s: frame.conductance_s,
          kappa_t_us_cm: frame.kappa_t_us_cm,
          kappa_25_us_cm: frame.kappa_25_us_cm,
          quality_flags: frame.quality_flags ?? undefined,
        };
        const arr = pointsRef.current;
        arr.push(p);
        if (arr.length > config.chart.maxPoints) {
          arr.splice(0, arr.length - config.chart.maxPoints);
        }
        if (runStartTRef.current === null) {
          runStartTRef.current = frame.timestamp;
        }
        setLatest(p);
        setCount(arr.length);
      }
      // 只在复位到 idle 时清空。续跑同一实验绝不能因 running 状态帧把曲线清掉。
      if (ev.type === 'status' && ev.status === 'idle') {
        pointsRef.current = [];
        runStartTRef.current = null;
        setCount(0);
        setLatest(null);
      }
    });
    return unsub;
  }, [bridge]);

  return { pointsRef, count, latest, runStartTRef, clearPoints, hydrateFromFrames };
}
