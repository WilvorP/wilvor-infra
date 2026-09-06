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
  /** Current-set total from `overview.recommendations.currentCount`. */
  currentCount: number | null | undefined;
  loading: boolean;
  failed: boolean;
}

/**
 * Compact preview of `overview.recommendations.latest`.
 *
 * After the current-set contract change those rows are the newest current
 * recommendations. The full operator list lives in Current attention.
 * The panel total is `currentCount`, not retained `activeCount`.
 */
export function RecommendationsPanel({
  recommendations,
  currentCount,
  loading,
  failed,
}: RecommendationsPanelProps) {
  const items = recommendations ?? [];

  return (
    <Panel
      title="Current recommendations"
      meta={
        failed || currentCount == null
          ? undefined
          : `${items.length} of ${currentCount}`
      }
    >
      {loading ? <LoadingState label="Loading recommendations" /> : null}

      {!loading && failed ? (
        <EmptyState
          title="Recommendations unavailable"
          detail="The overview request failed, so current advisories cannot be shown."
        />
      ) : null}

      {!loading && !failed && items.length === 0 ? (
        <EmptyState
          title="No current recommendations"
          detail="No advisory recommendation is tied to a current encounter."
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
