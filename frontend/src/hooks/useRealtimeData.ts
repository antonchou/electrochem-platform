import { useEffect, useRef, useState } from 'react';
import type { DataPoint } from '../types/protocol';
import { config } from '../config/config';
import type { ExperimentBridge } from '../services';

/**
 * 实时数据缓冲。
 * 数据点保存在 ref（可变数组）中，避免每次帧都触发大规模 re-render；
 * 通过 count/latest 的 setState（≈10Hz）驱动 UI 增量更新；
 * 曲线组件以固定间隔直接读 pointsRef 绘制。
 *
 * 数据分层：kt=κ(T) 主显示，k25=温补（未校准为 null），u/i/g 原始/派生诊断量。
 */
export function useRealtimeData(bridge: ExperimentBridge) {
  const pointsRef = useRef<DataPoint[]>([]);
  const [count, setCount] = useState(0);
  const [latest, setLatest] = useState<DataPoint | null>(null);
  const runStartTRef = useRef<number | null>(null);

  useEffect(() => {
    const unsub = bridge.subscribe((ev) => {
      if (ev.type === 'message') {
        const { frame } = ev;
        const p: DataPoint = {
          t: frame.timestamp ?? 0,
          // 主显示量：κ(T)（V1 兼容：ec = κ(T) 别名）
          kt: frame.kappa_t_us_cm ?? frame.ec ?? Number.NaN,
          // κ25：未校准（缺失/null）时为 null，UI 显示 "--"
          k25: frame.kappa_25_us_cm ?? null,
          tc: frame.temperature_raw_c ?? frame.temperature ?? Number.NaN,
          u: frame.voltage_raw_v ?? null,
          i: frame.current_raw_a !== undefined && frame.current_raw_a !== null ? frame.current_raw_a * 1000 : null,
          g: frame.conductance_s ?? null,
          freq: frame.excitation_frequency_hz,
          amp: frame.excitation_amplitude_v,
          rangeId: frame.range_id,
          sensorPathId: frame.sensor_path_id,
          calibrationId: frame.calibration_id,
          qualityFlags: frame.quality_flags,
        };
        const arr = pointsRef.current;
        arr.push(p);
        // 超出上限丢最旧点，防止长时间运行内存/渲染失控（P03/P04）
        if (arr.length > config.chart.maxPoints) {
          arr.splice(0, arr.length - config.chart.maxPoints);
        }
        if (runStartTRef.current === null) {
          runStartTRef.current = frame.timestamp ?? 0;
        }
        setLatest(p);
        setCount(arr.length);
      }
      // P1-2 修复：stopped/error 后直接开始，服务端会先广播 running 状态帧，
      // 此时清空上一轮缓冲，避免新旧两轮数据混合进曲线/统计/拟合。
      if (ev.type === 'status' && (ev.status === 'idle' || ev.status === 'running')) {
        pointsRef.current = [];
        runStartTRef.current = null;
        setCount(0);
        setLatest(null);
      }
    });
    return unsub;
  }, [bridge]);

  return { pointsRef, count, latest, runStartTRef };
}
