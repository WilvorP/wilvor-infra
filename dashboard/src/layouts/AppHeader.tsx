import { useIsFetching } from '@tanstack/react-query';

import { queryKeys } from '@/api/queryKeys';
import { FreshnessStrip } from '@/features/health/FreshnessStrip';

import styles from './AppHeader.module.css';

/**
 * Context bar: source freshness, the backend data time, polling state and
 * the advisory notice, on every route.
 *
 * "Live" here means polling is active, not that the data is instantaneous.
 * It sits beside the backend-generated timestamp the freshness readout
 * renders, so the operator judges currency from the data itself rather than
 * from the indicator.
 */
export function AppHeader() {
  const fetching = useIsFetching({ queryKey: queryKeys.all }) > 0;

  return (
    <header className={styles.header}>
      <div className={styles.freshness}>
        <FreshnessStrip />
      </div>

      <span
        className={`${styles.live} ${fetching ? styles.liveActive : ''}`}
        role="status"
      >
        <span className={styles.liveDot} aria-hidden="true" />
        {fetching ? 'Refreshing' : 'Live polling'}
      </span>

      <p className={styles.advisory} title="Wilvor is advisory decision support">
        <svg
          className={styles.advisoryIcon}
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          aria-hidden="true"
          focusable="false"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M12 11v5.5M12 7.6v.1" />
        </svg>
        Advisory only — not ATC
      </p>
    </header>
  );
}
