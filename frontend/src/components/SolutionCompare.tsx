import { useEffect, useMemo, useRef, useState } from 'react';
import * as echarts from 'echarts/core';
import { BarChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { IVAnalysis } from '../lib/ivAnalysis';
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
  status: string;
  experimentId: number | null;
  sampleId: string;
  live: IVAnalysis;
  simulated?: boolean;
}

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
 * 只累计「打开本页之后」做过的实验，不读取历史库，不插入虚构溶液。
 */
export function SolutionCompare({
  status,
  experimentId,
  sampleId,
  live,
  simulated = false,
}: Props) {
  const [sessionRows, setSessionRows] = useState<SolutionRow[]>([]);
  const chartEl = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (status !== 'stopped') return;
    const row = liveRow(sampleId, experimentId, live, simulated);
    if (!row || row.experimentId == null) return;
    const stored: SolutionRow = { ...row, isCurrent: false, key: `exp-${row.experimentId}` };
    setSessionRows((prev) => {
      const index = prev.findIndex((item) => item.experimentId === stored.experimentId);
      if (index === -1) return [...prev, stored];
      const next = prev.slice();
      next[index] = stored;
      return next;
    });
  }, [status, experimentId, sampleId, simulated, live]);

  const rows = useMemo(() => {
    const current = liveRow(sampleId, experimentId, live, simulated);
    const ordered: SolutionRow[] = [];
    const seen = new Set<number | null>();
    for (const row of sessionRows) {
      if (current && row.experimentId === current.experimentId) {
        ordered.push(current);
      } else {
        ordered.push({ ...row, isCurrent: false });
      }
      seen.add(row.experimentId);
    }
    if (current && !seen.has(current.experimentId)) ordered.push(current);
    return ordered;
  }, [sessionRows, live, sampleId, experimentId, simulated]);

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
      {!hasBars && (
        <p className={styles.hint} data-testid="compare-empty">
          从打开本页后的第一次实验开始比较。换溶液再测，这里会多一根柱；不会带入历史记录。
        </p>
      )}
      {anySimulated && hasBars && (
        <p className={styles.warn} data-testid="compare-simulated-note">
          当前数据源含 SIMULATED 标记，柱状图只含本页打开后测过的溶液，不是真实电极测量。
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
