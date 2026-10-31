import type { ReactNode } from 'react';

import styles from './Panel.module.css';

export interface PanelProps {
  title: string;
  /** Short qualifier rendered next to the title, e.g. a count. */
  meta?: ReactNode;
  /** Controls rendered on the right of the panel header. */
  actions?: ReactNode;
  children: ReactNode;
  /** Removes body padding for panels whose content manages its own layout. */
  flush?: boolean;
  /**
   * `card` (default) is a bordered surface. `section` is borderless and
   * inherits its parent's surface, for regions inside an already-divided
   * workspace where another box would only add noise.
   */
  variant?: 'card' | 'section';
  className?: string;
}

/** Content container with a dense header. */
export function Panel({
  title,
  meta,
  actions,
  children,
  flush = false,
  variant = 'card',
  className,
}: PanelProps) {
  return (
    <section
      className={`${styles.panel} ${styles[variant]} ${className ?? ''}`}
      aria-label={title}
    >
      <header className={styles.header}>
        <h2 className={styles.title}>{title}</h2>
        {meta ? <span className={`${styles.meta} wv-numeric`}>{meta}</span> : null}
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </header>
      <div className={flush ? styles.bodyFlush : styles.body}>{children}</div>
    </section>
  );
}
