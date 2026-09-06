import { StatusPill } from '@/components/StatusPill';
import { useFreshness } from '@/hooks/useOperationalQueries';
import { describeApiError } from '@/api/errors';
import { formatAge, formatUtcTime, NOT_REPORTED } from '@/utils/format';
import { presentFreshness } from '@/utils/status';

import { summariseFreshness } from './freshness';
import styles from './FreshnessStrip.module.css';

/**
 * Source freshness strip.
 *
 * Displayed on every operational screen so the operator can always tell how
 * old the underlying picture is. Age is shown alongside the status band
 * because the band alone hides the difference between a 10-second-old and a
 * 80-second-old aircraft picture, both of which report FRESH.
 */
export function FreshnessStrip() {
  const query = useFreshness();
  const summary = summariseFreshness(query.data);

  const hasData = query.data !== undefined;

  return (
    <div className={styles.strip}>
      <span className={styles.heading}>Sources</span>

      {!hasData && query.isPending ? (
        <span className={styles.pending}>Checking source freshness…</span>
      ) : null}

      {!hasData && query.isError ? (
        <span className={styles.error} role="alert">
          <span aria-hidden="true">⚠ </span>
          Source freshness unavailable. {describeApiError(query.error)}
        </span>
      ) : null}

      {hasData
        ? summary.sources.map((source) => (
            <span key={source.key} className={styles.source}>
              <StatusPill
                size="sm"
                prefix={source.label}
                presentation={presentFreshness(source.status)}
              />
              <span
                className={`${styles.age} wv-numeric`}
                title={
                  source.latestAt
                    ? `Latest record ${source.latestAt}`
                    : 'No timestamp reported'
                }
              >
                {source.ageSeconds === null
                  ? NOT_REPORTED
                  : formatAge(source.ageSeconds)}
              </span>
            </span>
          ))
        : null}

      <span className={styles.spacer} />

      {query.isError && hasData ? (
        <span className={styles.warning} role="status">
          <span aria-hidden="true">⚠ </span>
          Last refresh failed
        </span>
      ) : null}

      <span className={styles.generated}>
        <span className={styles.generatedLabel}>Freshness as of</span>
        <span className="wv-numeric">
          {formatUtcTime(summary.generatedAt)}
        </span>
      </span>
    </div>
  );
}
