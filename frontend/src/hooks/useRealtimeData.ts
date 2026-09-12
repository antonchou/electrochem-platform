import { useCallback, useEffect, useRef, useState } from 'react';
import type { DataPoint, RawFrame } from '../types/protocol';
import { config } from '../config/config';
import type { ExperimentBridge } from '../services';
import { rawFrameToPoint } from '../lib/ivAnalysis';

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
    // 去重基准取最后一个"有限 t"（P2-3）：t_seconds 为 null 的历史帧经 rawFrameToPoint
    // 变成 NaN，若它排在末尾，NaN 作基准会让 p.t > lastT 恒为 false，内存中的
    // 实时帧被整段丢弃。一个有限 t 都没有时取 -Infinity（全保留）。
    let lastT = Number.NEGATIVE_INFINITY;
    for (let i = converted.length - 1; i >= 0; i--) {
      if (Number.isFinite(converted[i].t)) {
        lastT = converted[i].t;
        break;
      }
    }
    const extra = pointsRef.current.filter((p) => p.t > lastT);
    const merged = converted.concat(extra);
    // 历史帧可能远超缓冲上限（DB 里 >2 万帧的实验），只保留最新一段，
    // 否则后续实时帧、拟合请求都会在超限缓冲上工作（拟合会被后端 2 万上限拒绝）。
    const clamped =
      merged.length > config.chart.maxPoints ? merged.slice(merged.length - config.chart.maxPoints) : merged;
    pointsRef.current = clamped;
    runStartTRef.current = clamped.length > 0 ? clamped[0].t : null;
    setCount(clamped.length);
    setLatest(clamped.length > 0 ? clamped[clamped.length - 1] : null);
  }, []);

  useEffect(() => {
    const unsub = bridge.subscribe((ev) => {
      if (ev.type === 'message') {
        const { frame } = ev;
        // frame.timestamp 经 parseServerMessage 校验必为有限数（P3-4 口径：
        // 实时入口在上游拒绝非法帧；历史入口 rawFrameToPoint 对 null 给 NaN 交给下游过滤）
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
          excitation_frequency_hz: frame.excitation_frequency_hz,
          excitation_amplitude_v: frame.excitation_amplitude_v,
          calibration_id: frame.calibration_id,
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
