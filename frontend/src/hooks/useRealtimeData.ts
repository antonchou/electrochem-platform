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
      if (ev.type === 'status' && ev.status === 'idle') {
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
