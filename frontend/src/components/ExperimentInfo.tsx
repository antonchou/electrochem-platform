import styles from './ExperimentInfo.module.css';

interface Props {
  title: string;
  startedAt: Date | null;
}

/** 实验信息：实验名称、状态（由页面用 StatusBadge 展示）、开始时间（任务书 §2） */
export function ExperimentInfo({ title, startedAt }: Props) {
  return (
    <div className={styles.info}>
      <h1 className={styles.title}>{title}</h1>
      <span className={styles.start}>
        开始时间：{startedAt ? startedAt.toLocaleTimeString() : '--'}
      </span>
    </div>
  );
}
