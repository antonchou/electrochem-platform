import styles from './ValueDisplay.module.css';

interface Props {
  label: string;
  /** null 表示尚无数据，显示 "--" */
  value: number | null;
  unit: string;
  precision?: number;
  /** 自定义数值格式化（默认 value.toFixed(precision)；用于科学计数法等场景） */
  format?: (v: number) => string;
  testId?: string;
}

/** 实时数值卡：单位必须明确显示（任务书 §2 / §4.3） */
export function ValueDisplay({ label, value, unit, precision = 1, format, testId }: Props) {
  const text = value === null ? '--' : format ? format(value) : value.toFixed(precision);
  return (
    <div className={styles.box} data-testid={testId}>
      <span className={styles.label}>{label}</span>
      <span className={styles.value}>{text}</span>
      <span className={styles.unit}>{unit}</span>
    </div>
  );
}
