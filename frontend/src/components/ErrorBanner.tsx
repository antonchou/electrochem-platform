import styles from './ErrorBanner.module.css';

interface Props {
  message: string | null;
  onDismiss: () => void;
}

/** 异常提示条：断线、坏数据、控制失败等有明确可理解提示，页面不崩溃（任务书 §2 / F10） */
export function ErrorBanner({ message, onDismiss }: Props) {
  if (!message) return null;
  return (
    <div className={styles.banner} data-testid="error-banner" role="alert">
      <span className={styles.text}>{message}</span>
      <button type="button" className={styles.close} onClick={onDismiss} aria-label="关闭提示">
        ×
      </button>
    </div>
  );
}
