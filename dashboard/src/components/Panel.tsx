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
  className?: string;
}

/** Bordered surface with a dense header. The primary content container. */
export function Panel({
  title,
  meta,
  actions,
  children,
  flush = false,
  className,
}: PanelProps) {
  return (
    <section
      className={`${styles.panel} ${className ?? ''}`}
      aria-label={title}
    >
      <header className={styles.header}>
        <h2 className={styles.title}>{title}</h2>
        {meta ? <span className={styles.meta}>{meta}</span> : null}
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </header>
      <div className={flush ? styles.bodyFlush : styles.body}>{children}</div>
    </section>
  );
}
