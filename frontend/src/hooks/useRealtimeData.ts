import { useEffect, useRef, useState } from 'react';
import type { DataPoint } from '../types/protocol';
import { config } from '../config/config';
import type { ExperimentBridge } from '../services';

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

  useEffect(() => {
    const unsub = bridge.subscribe((ev) => {
      if (ev.type === 'message') {
        const { frame } = ev;
        const p: DataPoint = {
          t: frame.timestamp,
          ec: frame.ec,
          tc: frame.temperature,
          // I–V 链路扩展（REQ-U-001 分层显示）：后端 v2+ 才下发，缺省为 undefined
          voltage_raw_v: frame.voltage_raw_v,
          current_raw_a: frame.current_raw_a,
          conductance_s: frame.conductance_s,
          kappa_t_us_cm: frame.kappa_t_us_cm,
          kappa_25_us_cm: frame.kappa_25_us_cm,
          quality_flags: frame.quality_flags ?? undefined,
        };
        const arr = pointsRef.current;
        arr.push(p);
        // 超出上限丢最旧点，防止长时间运行内存/渲染失控（P03/P04）
        if (arr.length > config.chart.maxPoints) {
          arr.splice(0, arr.length - config.chart.maxPoints);
        }
        if (runStartTRef.current === null) {
          runStartTRef.current = frame.timestamp;
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
