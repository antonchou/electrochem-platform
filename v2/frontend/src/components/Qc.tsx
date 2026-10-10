import { fixed, flagLabel, HARD_FLAGS, kappaText, percent, reasonLabel, VERDICT_LABELS } from '../lib/format.ts';
import type { Qc } from '../lib/types.ts';

export function VerdictBadge({ verdict, testId = 'verdict' }: { verdict: string | null | undefined; testId?: string }) {
  const cls = verdict ? verdict.toLowerCase() : 'none';
  return (
    <span className={`badge ${cls}`} data-testid={testId}>
      {verdict ? VERDICT_LABELS[verdict] ?? verdict : '未判定'}
    </span>
  );
}

export function Flags({ flags }: { flags: string[] }) {
  if (flags.length === 0) return null;
  return (
    <span className="flags" data-testid="flags">
      {flags.map((flag) => (
        <span key={flag} className={HARD_FLAGS.has(flag) ? 'flag hard' : 'flag'} title={flag} data-flag={flag}>
          {flagLabel(flag)}
        </span>
      ))}
    </span>
  );
}

/** 判稳结果的数字与原因。 */
export function QcSummary({ qc }: { qc: Qc }) {
  const sd = qc.kappa25_sd;
  return (
    <div className="qc">
      <dl className="facts">
        <dt>代表值 κ25</dt>
        <dd data-testid="representative">
          {qc.representative_kappa25 === null ? '—（不稳定，无代表值）' : kappaText(qc.representative_kappa25)}
          {qc.representative_kappa25 !== null && sd !== null ? ` ± ${fixed(sd, sd < 1 ? 3 : 1)}` : ''}
        </dd>
        <dt>有效点 / 窗口内</dt>
        <dd>
          {qc.n_valid} / {qc.n_points}（{fixed(qc.window_s, 1)} s）
        </dd>
        <dt>波动 CV</dt>
        <dd>{percent(qc.cv)}</dd>
        <dt>窗口内漂移</dt>
        <dd>{percent(qc.drift)}</dd>
        <dt>温度</dt>
        <dd>
          {fixed(qc.temperature_mean, 2)} °C{qc.temperature_span !== null ? `（跨度 ${fixed(qc.temperature_span, 2)} °C）` : ''}
        </dd>
      </dl>
      {qc.reasons.length > 0 && (
        <ul className="reasons">
          {qc.reasons.map((reason) => (
            <li key={reason}>
              {reasonLabel(reason)}
              {reason === 'hard_flags' && qc.hard_flags.length > 0 ? `：${qc.hard_flags.map(flagLabel).join('、')}` : ''}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
