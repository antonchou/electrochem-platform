import { useState, type ReactNode, type SyntheticEvent } from 'react';
import type { DataPoint } from '../types/protocol';
import { RealTimeChart } from './RealTimeChart';
import styles from './DiagnosticsPanel.module.css';

interface Props {
  pointsRef: React.MutableRefObject<DataPoint[]>;
  extras?: ReactNode;
}

/** EC-t 与其它专业量：默认折叠，主实验页不再把它当核心结果。 */
export function DiagnosticsPanel({ pointsRef, extras }: Props) {
  const [open, setOpen] = useState(false);
  const onToggle = (event: SyntheticEvent<HTMLDetailsElement>) => {
    setOpen(event.currentTarget.open);
  };

  return (
    <details className={styles.wrap} data-testid="diagnostics-panel" onToggle={onToggle}>
      <summary>详细数据 / 诊断（EC-t）</summary>
      <p className={styles.hint}>
        电导率-时间曲线用于看稳定性和噪声，不是比较不同溶液导电性的主图。
      </p>
      {extras ? <div className={styles.extras}>{extras}</div> : null}
      {open ? (
        <div className={styles.chart}>
          <RealTimeChart pointsRef={pointsRef} />
        </div>
      ) : null}
    </details>
  );
}
