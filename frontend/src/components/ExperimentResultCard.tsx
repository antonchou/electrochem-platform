import type { IVAnalysis } from '../lib/ivAnalysis';
import { formatConductanceS, formatConductivityUsCm, formatOhms } from '../lib/units';
import styles from './ExperimentResultCard.module.css';

interface Props {
  analysis: IVAnalysis;
  sampleId: string;
}

function text(value: string | null): string {
  return value ?? '--';
}

/** 本次实验的 G / R / κ / R²，数值全部来自缓冲或后端计算链。 */
export function ExperimentResultCard({ analysis, sampleId }: Props) {
  const g = analysis.conductanceS != null ? formatConductanceS(analysis.conductanceS).text : null;
  const r = analysis.resistanceOhm != null ? formatOhms(analysis.resistanceOhm) : null;
  const kappa = analysis.kappa25 != null ? formatConductivityUsCm(analysis.kappa25).text : null;
  const r2 = analysis.linearOk && analysis.r2 != null ? analysis.r2.toFixed(3) : '--';
  const t = analysis.meanTemperature != null ? `${analysis.meanTemperature.toFixed(2)} °C` : '--';

  return (
    <div className={styles.card} data-testid="iv-result-card">
      <div className={styles.item}>
        <span className={styles.label}>溶液</span>
        <span className={styles.value}>{sampleId || '--'}</span>
      </div>
      <div className={styles.item}>
        <span className={styles.label}>电导 G</span>
        <span className={styles.value} data-testid="iv-g">
          {text(g)}
        </span>
      </div>
      <div className={styles.item}>
        <span className={styles.label}>电阻 R</span>
        <span className={styles.value} data-testid="iv-r">
          {text(r)}
        </span>
      </div>
      <div className={styles.item}>
        <span className={styles.label}>电导率 κ25</span>
        <span className={styles.value} data-testid="iv-kappa">
          {text(kappa)}
        </span>
      </div>
      <div className={styles.item}>
        <span className={styles.label}>温度</span>
        <span className={styles.value}>{t}</span>
      </div>
      <div className={styles.item}>
        <span className={styles.label}>R²</span>
        <span className={styles.value}>{r2}</span>
      </div>
      <p className={styles.hint}>
        κ25 来自后端 I/U → κ 计算链，不是前端编造。
        {analysis.reason === 'voltage_span_too_small' ? ' G 为平均 I/U，不是扫描斜率。' : ''}
      </p>
    </div>
  );
}
