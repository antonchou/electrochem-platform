import { useCallback, useEffect, useState } from 'react';
import type { ExperimentStartOptions, ExperimentStatus } from '../types/protocol';
import type { ExperimentBridge } from '../services';

/**
 * 实验控制与状态管理（Phase 7：记录 experiment_id 用于导出/历史）。
 * 状态来源：控制接口响应 + 服务端状态帧 + 数据帧携带的 status。
 * 按钮可用/禁用规则：
 * - 开始：仅 idle/stopped/error 可用，running 时禁用（防重复触发，任务书 §4.3）
 * - 停止：仅 running 可用
 * - 重新开始：running 时禁用（先停止再重开）
 */
export function useExperiment(bridge: ExperimentBridge) {
  const [status, setStatus] = useState<ExperimentStatus>('idle');
  const [startedAt, setStartedAt] = useState<Date | null>(null);
  const [experimentId, setExperimentId] = useState<number | null>(null);
  const [sampleId, setSampleId] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    const unsub = bridge.subscribe((ev) => {
      if (ev.type === 'status') {
        setStatus(ev.status);
        if (ev.status === 'idle') {
          setStartedAt(null);
          setExperimentId(null);
          setSampleId('');
          setActionError(null);
        }
      }
      if (ev.type === 'message') {
        setStatus(ev.frame.status);
      }
    });
    return unsub;
  }, [bridge]);

  const run = useCallback(
    async (action: 'start' | 'stop' | 'reset', options?: ExperimentStartOptions) => {
      setBusy(true);
      setActionError(null);
      try {
        const res = await bridge.control(action, options);
        setStatus(res.status);
        if (action === 'start') {
          setStartedAt(new Date());
          if (res.experiment_id !== undefined) setExperimentId(res.experiment_id);
          if (res.sample_id !== undefined) setSampleId(res.sample_id);
        }
        if (action === 'reset') {
          setStartedAt(null);
          setExperimentId(null);
          setSampleId('');
        }
        return res;
      } catch (err) {
        const msg = err instanceof Error ? err.message : '控制请求失败';
        setActionError(msg);
        return { ok: false, status, message: msg } as const;
      } finally {
        setBusy(false);
      }
    },
    [bridge, status],
  );

  const start = useCallback(
    (options?: ExperimentStartOptions) => run('start', options),
    [run],
  );
  const stop = useCallback(() => run('stop'), [run]);
  const reset = useCallback(() => run('reset'), [run]);

  const canStart = !busy && status !== 'running';
  const canStop = !busy && status === 'running';
  const canReset = !busy && status !== 'running';

  return {
    status,
    startedAt,
    experimentId,
    sampleId,
    busy,
    actionError,
    setActionError,
    start,
    stop,
    reset,
    canStart,
    canStop,
    canReset,
  };
}
