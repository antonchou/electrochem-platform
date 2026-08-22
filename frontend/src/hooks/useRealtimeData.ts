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
  const runUidRef = useRef<string | null>(null);

  useEffect(() => {
    const clearBuffer = () => {
      pointsRef.current = [];
      runStartTRef.current = null;
      setCount(0);
      setLatest(null);
    };
    const unsub = bridge.subscribe((ev) => {
      if (ev.type === 'message') {
        const { frame } = ev;
        if (
          frame.experiment_uid &&
          runUidRef.current !== frame.experiment_uid &&
          pointsRef.current.length > 0
        ) {
          clearBuffer();
        }
        if (frame.experiment_uid) runUidRef.current = frame.experiment_uid;
        const p: DataPoint = {
          t: frame.t_seconds,
          // 在线链路只使用 V2 κ(T)，不允许回退到 V1 ec。
          kt: frame.kappa_t_us_cm,
          // κ25：未校准（缺失/null）时为 null，UI 显示 "--"
          k25: frame.kappa_25_us_cm,
          tc: frame.temperature_raw_c,
          u: frame.voltage_raw_v,
          i: frame.current_raw_a !== null ? frame.current_raw_a * 1000 : null,
          g: frame.conductance_s,
          freq: frame.excitation_frequency_hz ?? undefined,
          amp: frame.excitation_amplitude_v ?? undefined,
          rangeId: frame.range_id ?? undefined,
          sensorPathId: frame.sensor_path_id ?? undefined,
          calibrationId: frame.calibration_id ?? undefined,
          cellConstant: frame.cell_constant_cm_inv,
          calibrationValidUntil: frame.calibration_valid_until_utc,
          compensationModel: frame.compensation_model ?? undefined,
          alphaPerC: frame.alpha_per_c,
          qualityFlags: frame.quality_flags,
          concentration: frame.concentration_mmol_l ?? null,
        };
        const arr = pointsRef.current;
        arr.push(p);
        // 超出上限丢最旧点，防止长时间运行内存/渲染失控（P03/P04）
        if (arr.length > config.chart.maxPoints) {
          arr.splice(0, arr.length - config.chart.maxPoints);
        }
        if (runStartTRef.current === null) {
          runStartTRef.current = frame.t_seconds;
        }
        setLatest(p);
        setCount(arr.length);
      }
      // P1-2 修复：stopped/error 后直接开始，服务端会先广播 running 状态帧，
      // 此时清空上一轮缓冲，避免新旧两轮数据混合进曲线/统计/拟合。
      if (ev.type === 'status' && ev.status === 'idle') {
        clearBuffer();
        runUidRef.current = null;
      }
      if (ev.type === 'status' && ev.status === 'running') {
        if (!ev.experimentUid || ev.experimentUid !== runUidRef.current) clearBuffer();
        runUidRef.current = ev.experimentUid ?? null;
      }
    });
    return unsub;
  }, [bridge]);

  return { pointsRef, count, latest, runStartTRef };
}
