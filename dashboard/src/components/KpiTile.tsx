import type { ReactNode } from 'react';

import styles from './KpiTile.module.css';

export interface KpiBreakdownEntry {
  label: string;
  value: string;
  tone?: 'high' | 'medium' | 'low' | 'muted';
}

export interface KpiTileProps {
  label: string;
  value: string;
  /** Unit or qualifier rendered after the value, e.g. "active". */
  unit?: string;
  breakdown?: readonly KpiBreakdownEntry[];
  /** Rendered instead of the value when the metric could not be loaded. */
  problem?: ReactNode;
  /** Marks the value as carried over from an earlier successful refresh. */
  stale?: boolean;
}

/**
 * Single operational metric.
 *
 * Values are rendered in tabular numerals so the row does not reflow as
 * counts change on each poll.
 */
export function KpiTile({
  label,
  value,
  unit,
  breakdown,
  problem,
  stale = false,
}: KpiTileProps) {
  return (
    <div className={styles.tile}>
      <p className={styles.label}>{label}</p>

      {problem ? (
        <p className={styles.problem}>{problem}</p>
      ) : (
        <p className={styles.valueRow}>
          <span className={`${styles.value} wv-numeric`}>{value}</span>
          {unit ? <span className={styles.unit}>{unit}</span> : null}
          {stale ? (
            <span className={styles.stale} title="Awaiting a newer refresh">
              stale
            </span>
          ) : null}
        </p>
      )}

      {breakdown && breakdown.length > 0 ? (
        <dl className={styles.breakdown}>
          {breakdown.map((entry) => (
            <div key={entry.label} className={styles.breakdownEntry}>
              <dt className={styles.breakdownLabel}>{entry.label}</dt>
              <dd
                className={`${styles.breakdownValue} ${
                  entry.tone ? styles[entry.tone] : ''
                } wv-numeric`}
              >
                {entry.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </div>
  );
}
