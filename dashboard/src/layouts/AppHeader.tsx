import { useIsFetching } from '@tanstack/react-query';

import { StatusPill } from '@/components/StatusPill';
import { queryKeys } from '@/api/queryKeys';
import { useFreshness, useSystemHealth } from '@/hooks/useOperationalQueries';
import { formatUtcTime } from '@/utils/format';
import { presentSystemStatus } from '@/utils/status';

import styles from './AppHeader.module.css';

/**
 * Console header.
 *
 * "Live" here means polling is active, not that the data is instantaneous.
 * The label is therefore paired with a backend-generated timestamp so the
 * operator judges currency from the data itself rather than from the
 * indicator.
 *
 * The timestamp comes from `/freshness`, which the shell already polls on
 * every route via the freshness strip. TanStack Query dedupes the two
 * subscriptions into one request, so this adds no API traffic.
 */
export function AppHeader() {
  const fetching = useIsFetching({ queryKey: queryKeys.all }) > 0;
  const health = useSystemHealth();
  const freshness = useFreshness();

  return (
    <header className={styles.header}>
      <div className={styles.brand}>
        <span className={styles.mark} aria-hidden="true" />
        <span className={styles.wordmark}>WILVOR</span>
        <span className={styles.product}>Operations</span>
      </div>

      <span
        className={`${styles.live} ${fetching ? styles.liveActive : ''}`}
        role="status"
      >
        <span className={styles.liveDot} aria-hidden="true" />
        {fetching ? 'Refreshing' : 'Live polling'}
      </span>

      <div className={styles.spacer} />

      <div className={styles.meta}>
        <span className={styles.metaLabel}>Data generated</span>
        <span className={`${styles.metaValue} wv-numeric`}>
          {formatUtcTime(freshness.data?.generatedAt)}
        </span>
      </div>

      <StatusPill
        size="sm"
        prefix="Platform"
        presentation={presentSystemStatus(
          health.isError ? null : health.data?.status,
        )}
      />

      <p className={styles.advisory} title="Wilvor is advisory decision support">
        Advisory only — not ATC
      </p>
    </header>
  );
}
