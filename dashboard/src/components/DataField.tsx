import type { ReactNode } from 'react';

import { NOT_REPORTED } from '@/utils/format';

import styles from './DataField.module.css';

export interface DataFieldProps {
  label: string;
  value: ReactNode;
  /** Renders the value in tabular numerals. Use for measured quantities. */
  numeric?: boolean;
}

/**
 * Label/value pair for investigation surfaces.
 *
 * A missing value renders an explicit "not reported" marker rather than a
 * blank, so an absent attribute is never mistaken for a zero or an empty
 * measurement.
 */
export function DataField({ label, value, numeric = false }: DataFieldProps) {
  const isMissing =
    value === null || value === undefined || value === NOT_REPORTED;

  return (
    <div className={styles.field}>
      <dt className={styles.label}>{label}</dt>
      <dd
        className={[
          styles.value,
          numeric ? 'wv-numeric' : '',
          isMissing ? styles.missing : '',
        ]
          .filter(Boolean)
          .join(' ')}
      >
        {isMissing ? NOT_REPORTED : value}
        {isMissing ? (
          <span className="wv-visually-hidden">Not reported</span>
        ) : null}
      </dd>
    </div>
  );
}

export interface DataFieldGridProps {
  children: ReactNode;
  columns?: 1 | 2 | 3;
}

export function DataFieldGrid({ children, columns = 2 }: DataFieldGridProps) {
  return (
    <dl className={`${styles.grid} ${styles[`columns${columns}`]}`}>
      {children}
    </dl>
  );
}
