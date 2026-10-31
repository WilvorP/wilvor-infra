import type { StatusPresentation } from '@/utils/status';

import styles from './StatusPill.module.css';

export interface StatusPillProps {
  presentation: StatusPresentation;
  /** Optional prefix, e.g. the source name in the freshness strip. */
  prefix?: string;
  size?: 'sm' | 'md';
  /**
   * `soft` (default) sits on a tinted chip. `plain` drops the chip and keeps
   * the toned glyph and label, for dense lists where many statuses repeat.
   */
  variant?: 'soft' | 'plain';
}

/**
 * Status indicator.
 *
 * The glyph and the text label are both always rendered. Aviation status must
 * remain readable without colour perception, in monochrome, and to screen
 * readers, so tone is strictly supplementary here.
 */
export function StatusPill({
  presentation,
  prefix,
  size = 'md',
  variant = 'soft',
}: StatusPillProps) {
  return (
    <span
      className={`${styles.pill} ${styles[presentation.tone]} ${styles[size]} ${styles[variant]}`}
    >
      <span className={styles.glyph} aria-hidden="true">
        {presentation.glyph}
      </span>
      {prefix ? <span className={styles.prefix}>{prefix}</span> : null}
      <span className={styles.label}>{presentation.label}</span>
    </span>
  );
}
