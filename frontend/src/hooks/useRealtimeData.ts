import { useCallback, useEffect, useRef, useState } from 'react';
import type { DataPoint, RawFrame } from '../types/protocol';
import { config } from '../config/config';
import type { ExperimentBridge } from '../services';
import { rawFrameToPoint } from '../lib/ivAnalysis';
import { RealtimeBuffer } from '../lib/realtimeBuffer';

/**
 * 实时数据缓冲。
 * 数据点保存在 ref（可变数组）中，避免每次帧都触发大规模 re-render；
 * 通过 count/latest/revision 的 setState（≈10Hz）驱动 UI 增量更新；
 * 曲线组件以固定间隔直接读 pointsRef 绘制。
 *
 * - 派生计算（I–V 分析等）依赖 revision：封顶后 count 恒为上限，不能当“数据变了”的信号。
 * - 缓冲按实验隔离：帧、状态帧、重连时的当前实验都会对齐归属，换实验先清空。
 */
export function useRealtimeData(bridge: ExperimentBridge) {
  const [buffer] = useState(() => new RealtimeBuffer(config.chart.maxPoints));
  const pointsRef = useRef<DataPoint[]>(buffer.points);
  const [count, setCount] = useState(0);
  const [revision, setRevision] = useState(0);
  const [latest, setLatest] = useState<DataPoint | null>(null);

  // 把缓冲现状同步到 ref（图表定时读取）与 React 状态
  const sync = useCallback(() => {
    const pts = buffer.points;
    pointsRef.current = pts;
    setCount(pts.length);
    setRevision(buffer.revision);
    setLatest(pts.length > 0 ? pts[pts.length - 1] : null);
  }, [buffer]);

  const clearPoints = useCallback(() => {
    buffer.clear();
    sync();
  }, [buffer, sync]);

  const hydrateFromFrames = useCallback(
    (frames: RawFrame[], experimentId?: number | null) => {
      // 历史帧可能远超缓冲上限（DB 里 >2 万帧的实验），hydrate 只保留最新一段，
      // 否则后续实时帧、拟合请求都会在超限缓冲上工作（拟合会被后端 2 万上限拒绝）。
      buffer.hydrate(frames.map(rawFrameToPoint), experimentId ?? undefined);
      sync();
    },
    [buffer, sync],
  );

  const bindExperiment = useCallback(
    (experimentId: number | null | undefined) => {
      if (buffer.bindExperiment(experimentId)) sync();
    },
    [buffer, sync],
  );

  useEffect(() => {
    const unsub = bridge.subscribe((ev) => {
      if (ev.type === 'message') {
        const { frame } = ev;
        // frame.timestamp 经 parseServerMessage 校验必为有限数（P3-4 口径：
        // 实时入口在上游拒绝非法帧；历史入口 rawFrameToPoint 对 null 给 NaN 交给下游过滤）
        buffer.push(
          {
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
          },
          frame.experiment_id,
        );
        sync();
      }
      if (ev.type === 'status' && buffer.applyStatus(ev.status, ev.experiment_id)) {
        // 规则见 RealtimeBuffer.applyStatus：idle 复位清空；换了实验先清旧点
        sync();
      }
      if (ev.type === 'connection' && ev.status === 'connected') {
        // 重连后对齐归属：断线/后端重启期间可能已换了实验（规则见 RealtimeBuffer.alignToCurrent）
        const generation = buffer.generation;
        bridge.api
          .getCurrentExperiment()
          .then((cur) => {
            if (buffer.alignToCurrent(cur, generation)) sync();
          })
          .catch(() => {
            /* 查询失败不阻断实时流；下一帧自带实验 id 仍会对齐 */
          });
      }
    });
    return unsub;
  }, [bridge, buffer, sync]);

  return {
    pointsRef,
    count,
    revision,
    latest,
    clearPoints,
    hydrateFromFrames,
    bindExperiment,
  };
}
