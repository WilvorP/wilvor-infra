import { Panel } from '@/components/Panel';
import { EmptyState, LoadingState } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import type { RiskResult } from '@/types/api';
import { asString } from '@/utils/coerce';
import {
  formatAircraftLabel,
  formatRiskScore,
  formatUtcTime,
  humaniseToken,
  NOT_REPORTED,
} from '@/utils/format';
import { presentRiskLevel } from '@/utils/status';

import styles from './FeedPanel.module.css';

export interface TopRisksPanelProps {
  risks: readonly RiskResult[] | undefined;
  loading: boolean;
  failed: boolean;
}

/**
 * Highest-ranked current risk evaluations, taken from `overview.topRisks`.
 *
 * The backend already ranks these by level, then score, then recency, so the
 * order is preserved rather than re-sorted here.
 */
export function TopRisksPanel({ risks, loading, failed }: TopRisksPanelProps) {
  const items = risks ?? [];

  return (
    <Panel title="Operational risks" meta={failed ? undefined : `top ${items.length}`}>
      {loading ? <LoadingState label="Loading risk evaluations" /> : null}

      {!loading && failed ? (
        <EmptyState
          title="Risk evaluations unavailable"
          detail="The overview request failed, so current risk ranking cannot be shown."
        />
      ) : null}

      {!loading && !failed && items.length === 0 ? (
        <EmptyState
          title="No active risk evaluations"
          detail="No encounter currently has a valid risk result. This is a normal quiet state, not a data failure."
        />
      ) : null}

      {!loading && !failed && items.length > 0 ? (
        <ul className={styles.list}>
          {items.map((risk, index) => {
            const aircraftId = asString(risk.aircraft_id);

            return (
              <li
                key={asString(risk.risk_id) ?? `risk-${index}`}
                className={styles.row}
              >
                <div className={styles.rowHeader}>
                  <StatusPill
                    size="sm"
                    presentation={presentRiskLevel(risk.risk_level)}
                  />
                  <span className={`${styles.score} wv-numeric`}>
                    {formatRiskScore(risk.risk_score)}
                    <span className={styles.scoreMax}>/100</span>
                  </span>
                  <span className={`${styles.timestamp} wv-numeric`}>
                    {formatUtcTime(risk.generated_at_utc)}
                  </span>
                </div>

                <div className={styles.rowBody}>
                  <span className={styles.primary}>
                    {formatAircraftLabel(undefined, aircraftId)}
                  </span>
                  <span className={styles.separator} aria-hidden="true">
                    ·
                  </span>
                  <span className={styles.secondary}>
                    {humaniseToken(risk.hazard_type)}
                  </span>
                  <span className={styles.separator} aria-hidden="true">
                    ·
                  </span>
                  <span className={styles.secondary}>
                    Confidence {asString(risk.confidence) ?? NOT_REPORTED}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </Panel>
  );
}
