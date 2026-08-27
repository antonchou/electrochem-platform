import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import { BarChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { ApiClient } from '../services/apiClient';
import type { IVAnalysis } from '../lib/ivAnalysis';
import { analyzeIV, rawFrameToPoint } from '../lib/ivAnalysis';
import { formatConductanceS, formatConductivityUsCm, formatOhms } from '../lib/units';
import styles from './SolutionCompare.module.css';

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);

export interface SolutionRow {
  key: string;
  experimentId: number | null;
  solutionName: string;
  conductanceS: number | null;
  resistanceOhm: number | null;
  kappa25: number | null;
  temperatureC: number | null;
  r2: number | null;
  isCurrent: boolean;
  simulated: boolean;
}

interface Props {
  api: ApiClient | null;
  status: string;
  experimentId: number | null;
  sampleId: string;
  live: IVAnalysis;
  simulated?: boolean;
}

const MAX_HISTORY = 8;

function liveRow(
  sampleId: string,
  experimentId: number | null,
  live: IVAnalysis,
  simulated: boolean,
): SolutionRow | null {
  if (live.n <= 0 || live.kappa25 == null) return null;
  return {
    key: experimentId != null ? `current-${experimentId}` : 'current',
    experimentId,
    solutionName: sampleId.trim() || '当前溶液',
    conductanceS: live.conductanceS,
    resistanceOhm: live.resistanceOhm,
    kappa25: live.kappa25,
    temperatureC: live.meanTemperature,
    r2: live.linearOk ? live.r2 : null,
    isCurrent: true,
    simulated,
  };
}

function fmtKappa(value: number | null): string {
  return value != null && Number.isFinite(value) ? formatConductivityUsCm(value).text : '--';
}

function fmtG(value: number | null): string {
  return value != null && Number.isFinite(value) ? formatConductanceS(value).text : '--';
}

function fmtR(value: number | null): string {
  return value != null && Number.isFinite(value) ? formatOhms(value) : '--';
}

/**
 * 不同溶液 κ 比较：柱状图 + 表。
 * 只展示后端历史实验与当前测量，不插入虚构的 NaCl/蒸馏水。
 */
export function SolutionCompare({
  api,
  status,
  experimentId,
  sampleId,
  live,
  simulated = false,
}: Props) {
  const [history, setHistory] = useState<SolutionRow[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const chartEl = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!api || status === 'running') return;
    let cancelled = false;
    (async () => {
      try {
        const list = await api.listExperiments();
        const stopped = list
          .filter((item) => item.status === 'stopped' || item.status === 'aborted')
          .slice(0, MAX_HISTORY);
        const rows = await Promise.all(
          stopped.map(async (item) => {
            const [detail, frames] = await Promise.all([
              api.getExperiment(item.id),
              api.getFrames(item.id, 4000).catch(() => []),
            ]);
            const sample = detail.samples[0];
            const analysis = analyzeIV(frames.map(rawFrameToPoint));
            const flags = frames.map((f) => f.quality_flags ?? '').join('|');
            const kappa =
              sample?.representative_value ?? sample?.k25_median ?? analysis.kappa25 ?? null;
            return {
              key: `exp-${item.id}`,
              experimentId: item.id,
              solutionName: sample?.sample_id || item.sample_id || `实验 ${item.id}`,
              conductanceS: analysis.conductanceS,
              resistanceOhm: analysis.resistanceOhm,
              kappa25: kappa,
              temperatureC: analysis.meanTemperature,
              r2: analysis.linearOk ? analysis.r2 : null,
              isCurrent: experimentId === item.id,
              simulated: flags.includes('SIMULATED'),
            } satisfies SolutionRow;
          }),
        );
        if (!cancelled) {
          setHistory(rows);
          setLoadError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setHistory([]);
          setLoadError(err instanceof Error ? err.message : '无法读取历史溶液');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [api, status, experimentId]);

  const rows = useMemo(() => {
    const current = liveRow(sampleId, experimentId, live, simulated);
    const withoutCurrent = history.filter((row) => row.experimentId !== experimentId);
    return current ? [current, ...withoutCurrent] : withoutCurrent;
  }, [history, live, sampleId, experimentId, simulated]);

  const names = useMemo(() => {
    const seen = new Map<string, number>();
    return rows.map((row) => {
      const base = row.solutionName;
      const n = (seen.get(base) ?? 0) + 1;
      seen.set(base, n);
      return n === 1 ? base : `${base} (#${row.experimentId ?? n})`;
    });
  }, [rows]);

  const yUnit = useMemo(() => {
    const maxAbs = Math.max(
      0,
      ...rows.map((row) => row.kappa25).filter((v): v is number => v != null).map(Math.abs),
    );
    return maxAbs >= 1000 ? ('mS/cm' as const) : ('μS/cm' as const);
  }, [rows]);

  const barSeries = useMemo(
    () =>
      rows.map((row) => {
        const raw = row.kappa25;
        const value = raw == null || !Number.isFinite(raw) ? null : yUnit === 'mS/cm' ? raw / 1000 : raw;
        return {
          value,
          itemStyle: { color: row.isCurrent ? '#2f6fed' : '#64748b' },
        };
      }),
    [rows, yUnit],
  );

  useEffect(() => {
    const el = chartEl.current;
    if (!el) return;
    const chart = echarts.init(el);
    chartRef.current = chart;
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(onResize) : null;
    observer?.observe(el);
    return () => {
      window.removeEventListener('resize', onResize);
      observer?.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.setOption(
      {
        animation: false,
        tooltip: { trigger: 'axis' },
        grid: { left: 48, right: 16, top: 16, bottom: 48 },
        xAxis: {
          type: 'category',
          data: names,
          axisLabel: { interval: 0, rotate: names.length > 4 ? 20 : 0 },
        },
        yAxis: {
          type: 'value',
          name: `电导率 κ (${yUnit})`,
          nameGap: 8,
        },
        series: [
          {
            name: 'κ',
            type: 'bar',
            data: barSeries,
            barMaxWidth: 48,
          },
        ],
      },
      { notMerge: true },
    );
  }, [names, barSeries, yUnit]);

  const anySimulated = rows.some((row) => row.simulated);
  const hasBars = barSeries.some((item) => item.value != null);

  return (
    <div className={styles.wrap} data-testid="solution-compare">
      {!api && (
        <p className={styles.hint}>浏览器模拟模式没有历史溶液接口，这里只显示本次测量。</p>
      )}
      {loadError && <p className={styles.hint}>{loadError}</p>}
      {!hasBars && (
        <p className={styles.hint} data-testid="compare-empty">
          完成至少一次实验后，不同溶液会出现在这里。不会用模拟溶液冒充实测。
        </p>
      )}
      {anySimulated && hasBars && (
        <p className={styles.warn} data-testid="compare-simulated-note">
          当前数据源含 SIMULATED 标记，柱状图来自本机已完成的实验，不是真实电极测量。
        </p>
      )}
      <div ref={chartEl} className={styles.chart} data-testid="compare-chart" />
      <div className={styles.tableWrap}>
        <table className={styles.table} data-testid="compare-table">
          <thead>
            <tr>
              <th>溶液</th>
              <th>电导 G</th>
              <th>电阻 R</th>
              <th>电导率 κ</th>
              <th>温度</th>
              <th>R²</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6}>暂无溶液结果</td>
              </tr>
            ) : (
              rows.map((row, i) => (
                <tr key={row.key} className={row.isCurrent ? styles.current : undefined}>
                  <td>{names[i]}</td>
                  <td>{fmtG(row.conductanceS)}</td>
                  <td>{fmtR(row.resistanceOhm)}</td>
                  <td>{fmtKappa(row.kappa25)}</td>
                  <td>{row.temperatureC != null ? `${row.temperatureC.toFixed(2)} °C` : '--'}</td>
                  <td>{row.r2 != null && Number.isFinite(row.r2) ? row.r2.toFixed(3) : '--'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
