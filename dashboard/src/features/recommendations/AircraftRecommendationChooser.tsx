import { StatusPill } from '@/components/StatusPill';
import type { Recommendation } from '@/types/api';
import { asString } from '@/utils/coerce';
import { formatAircraftLabel, humaniseToken, NOT_REPORTED } from '@/utils/format';
import {
  presentRecommendationAction,
  presentRiskLevel,
} from '@/utils/status';

import { recommendationIdOf } from './recommendationList';
import styles from './AircraftRecommendationChooser.module.css';

export interface AircraftRecommendationChooserProps {
  aircraftId: string;
  callsign: string | null;
  items: readonly Recommendation[];
  selectedRecommendationId: string | null;
  onSelect: (recommendationId: string) => void;
  onDismiss: () => void;
}

/**
 * Compact chooser for a map-selected aircraft that has several current
 * loaded recommendations. Selection is by `recommendation_id` only.
 */
export function AircraftRecommendationChooser({
  aircraftId,
  callsign,
  items,
  selectedRecommendationId,
  onSelect,
  onDismiss,
}: AircraftRecommendationChooserProps) {
  return (
    <div
      className={styles.chooser}
      role="region"
      aria-label="Current recommendations for this aircraft"
    >
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>Current recommendations</h2>
          <p className={styles.meta}>
            <span className={styles.callsign}>
              {formatAircraftLabel(callsign, aircraftId)}
            </span>
            <span className={`${styles.id} wv-numeric`}>
              {aircraftId.toUpperCase()}
            </span>
            <span>
              {items.length.toLocaleString('en-US')} current loaded — choose one
            </span>
          </p>
        </div>
        <button type="button" className={styles.dismiss} onClick={onDismiss}>
          Dismiss
        </button>
      </header>

      <ul className={styles.list}>
        {items.map((item) => {
          const recommendationId = recommendationIdOf(item);
          const hazardId = asString(item.hazard_id) ?? NOT_REPORTED;
          const selected =
            recommendationId !== null &&
            recommendationId === selectedRecommendationId;

          return (
            <li key={recommendationId ?? hazardId}>
              <button
                type="button"
                className={`${styles.row} ${selected ? styles.rowSelected : ''}`}
                disabled={recommendationId === null}
                aria-current={selected ? 'true' : undefined}
                onClick={() => {
                  if (recommendationId !== null) {
                    onSelect(recommendationId);
                  }
                }}
              >
                <span className={`${styles.hazard} wv-numeric`}>{hazardId}</span>
                <StatusPill
                  size="sm"
                  presentation={presentRecommendationAction(
                    item.primary_action_type,
                  )}
                />
                <StatusPill
                  size="sm"
                  presentation={presentRiskLevel(item.risk_level)}
                />
                <span className={styles.type}>
                  {humaniseToken(item.recommendation_status)}
                </span>
                {selected ? (
                  <span className={styles.selectedMark}>Selected</span>
                ) : null}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
