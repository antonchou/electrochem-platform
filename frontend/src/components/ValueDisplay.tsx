import { fmtFixed } from '../lib/units';
import styles from './ValueDisplay.module.css';

interface Props {
  label: string;
  /** null 表示尚无数据，显示 "--" */
  value: number | null;
  unit: string;
  precision?: number;
  testId?: string;
}

/** 实时数值卡：单位必须明确显示（任务书 §2 / §4.3） */
export function ValueDisplay({ label, value, unit, precision = 1, testId }: Props) {
  return (
    <div className={styles.box} data-testid={testId}>
      <span className={styles.label}>{label}</span>
      <span className={styles.value} data-testid={testId ? `${testId}-num` : undefined}>
        {/* NaN / ±Infinity 一律显示 "--"：脏数据不得直接暴露到读数卡（T-12） */}
        {fmtFixed(value, precision)}
      </span>
      <span className={styles.unit}>{unit}</span>
    </div>
  );
}
