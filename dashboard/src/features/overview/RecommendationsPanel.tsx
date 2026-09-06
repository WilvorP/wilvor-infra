import { Panel } from '@/components/Panel';
import { EmptyState, LoadingState } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import type { OverviewRecommendationSummary } from '@/types/api';
import { asString } from '@/utils/coerce';
import {
  formatAircraftLabel,
  formatUtcTime,
  humaniseToken,
  NOT_REPORTED,
} from '@/utils/format';
import { presentRiskLevel } from '@/utils/status';

import styles from './FeedPanel.module.css';

export interface RecommendationsPanelProps {
  recommendations: readonly OverviewRecommendationSummary[] | undefined;
  activeCount: number | null | undefined;
  loading: boolean;
  failed: boolean;
}

/**
 * Latest active advisory recommendations from `overview.recommendations.latest`.
 *
 * This is the backend's own camelCase projection of the recommendation
 * records, not the raw snake_case items returned by
 * `GET /recommendations/active`.
 */
export function RecommendationsPanel({
  recommendations,
  activeCount,
  loading,
  failed,
}: RecommendationsPanelProps) {
  const items = recommendations ?? [];

  return (
    <Panel
      title="Recommendations"
      meta={
        failed || activeCount == null
          ? undefined
          : `${items.length} of ${activeCount}`
      }
    >
      {loading ? <LoadingState label="Loading recommendations" /> : null}

      {!loading && failed ? (
        <EmptyState
          title="Recommendations unavailable"
          detail="The overview request failed, so active advisories cannot be shown."
        />
      ) : null}

      {!loading && !failed && items.length === 0 ? (
        <EmptyState
          title="No active recommendations"
          detail="No advisory recommendation is currently valid."
        />
      ) : null}

      {!loading && !failed && items.length > 0 ? (
        <ul className={styles.list}>
          {items.map((item, index) => (
            <li
              key={asString(item.recommendationId) ?? `rec-${index}`}
              className={styles.row}
            >
              <div className={styles.rowHeader}>
                <StatusPill
                  size="sm"
                  presentation={presentRiskLevel(item.riskLevel)}
                />
                <span className={styles.action}>
                  {humaniseToken(item.action)}
                </span>
                <span className={`${styles.timestamp} wv-numeric`}>
                  until {formatUtcTime(item.validUntilUtc)}
                </span>
              </div>

              <div className={styles.rowBody}>
                <span className={styles.primary}>
                  {formatAircraftLabel(undefined, item.aircraftId)}
                </span>
                <span className={styles.separator} aria-hidden="true">
                  ·
                </span>
                <span className={styles.secondary}>
                  {asString(item.preferredAirportId)
                    ? `Preferred ${asString(item.preferredAirportId)}`
                    : 'No preferred airport'}
                </span>
                <span className={styles.separator} aria-hidden="true">
                  ·
                </span>
                <span className={styles.secondary}>
                  Confidence {asString(item.confidence) ?? NOT_REPORTED}
                </span>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}
