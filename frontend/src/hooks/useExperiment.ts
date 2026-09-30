import { useCallback, useEffect, useRef, useState } from 'react';
import { errorFromControlResponse } from '../lib/controlError';
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
  const [persistDegraded, setPersistDegraded] = useState(false);
  // 同步镜像 experimentId：applyCurrent 是稳定回调，要靠它判断“是否换了实验”
  const experimentIdRef = useRef<number | null>(null);

  const setExp = useCallback((id: number | null) => {
    experimentIdRef.current = id;
    setExperimentId(id);
  }, []);

  const applyCurrent = useCallback(
    (payload: {
      status: ExperimentStatus;
      experiment_id?: number | null;
      sample_id?: string | null;
      persistence?: string | null;
    }) => {
      setStatus(payload.status);
      if (payload.persistence === 'degraded') setPersistDegraded(true);
      else if (payload.persistence === 'ok') setPersistDegraded(false);
      if (payload.status === 'idle') {
        setStartedAt(null);
        setExp(null);
        setSampleId('');
        return;
      }
      const id = payload.experiment_id;
      if (id !== undefined && id !== null && id !== experimentIdRef.current) {
        // 换了实验（旁观端看到别处开新实验 / 重连后已是另一条实验）：
        // 开始时间不能沿用上一条实验的，按本端首次看到重算（09-30 审查 #3）
        const switched = experimentIdRef.current !== null;
        setExp(id);
        if (switched) setStartedAt(payload.status === 'running' ? new Date() : null);
      }
      if (payload.sample_id) setSampleId(payload.sample_id);
      if (payload.status === 'running') {
        setStartedAt((prev) => prev ?? new Date());
      }
    },
    [setExp],
  );

  useEffect(() => {
    const unsub = bridge.subscribe((ev) => {
      if (ev.type === 'status') {
        applyCurrent({
          status: ev.status,
          experiment_id: ev.experiment_id,
          sample_id: ev.sample_id,
          persistence: ev.persistence,
        });
        if (ev.status === 'idle' && !ev.message) setActionError(null);
        if (ev.message) setActionError(ev.message);
      }
      if (ev.type === 'message') {
        setStatus(ev.frame.status);
        if (ev.frame.quality_flags?.includes('PERSIST_DROPPED')) setPersistDegraded(true);
      }
      if (ev.type === 'connection' && ev.status === 'connected' && bridge.api) {
        bridge.api
          .getCurrentExperiment()
          .then((cur) => {
            applyCurrent(cur);
            if (cur.persistence === 'degraded' && cur.message) setActionError(cur.message);
          })
          .catch(() => {
            /* 重连恢复失败不阻断实时流 */
          });
      }
    });
    return unsub;
  }, [bridge, applyCurrent]);

  const run = useCallback(
    async (action: 'start' | 'stop' | 'reset', options?: ExperimentStartOptions) => {
      setBusy(true);
      setActionError(null);
      try {
        const res = await bridge.control(action, options);
        const banner = errorFromControlResponse(res);
        if (banner) setActionError(banner);
        if (!res.ok) {
          setStatus(res.status);
          return res;
        }
        if (res.persistence === 'degraded') setPersistDegraded(true);
        else if (res.persistence === 'ok') setPersistDegraded(false);
        setStatus(res.status);
        if (action === 'start') {
          if (!res.resumed) setStartedAt(new Date());
          if (res.experiment_id !== undefined && res.experiment_id !== null) {
            setExp(res.experiment_id);
          }
          if (res.sample_id !== undefined) setSampleId(res.sample_id);
        }
        if (action === 'stop' && res.experiment_id !== undefined && res.experiment_id !== null) {
          setExp(res.experiment_id);
        }
        if (action === 'reset') {
          setStartedAt(null);
          setExp(null);
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
    [bridge, status, setExp],
  );

  const start = useCallback(
    (options?: ExperimentStartOptions) => run('start', options),
    [run],
  );
  const stop = useCallback(() => run('stop'), [run]);
  const reset = useCallback(() => run('reset'), [run]);
  const restart = useCallback(
    async (options?: ExperimentStartOptions) => {
      // reset 未生效（网络失败/后端拒绝）时不得继续 start，否则“旧实验还在跑却开新实验”
      const resetRes = await run('reset');
      if (!('ok' in resetRes) || !resetRes.ok) return resetRes;
      return run('start', options);
    },
    [run],
  );

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
    persistDegraded,
    setActionError,
    start,
    stop,
    reset,
    restart,
    canStart,
    canStop,
    canReset,
  };
}
