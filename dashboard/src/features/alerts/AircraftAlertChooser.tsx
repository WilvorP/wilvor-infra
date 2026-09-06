import { StatusPill } from '@/components/StatusPill';
import type { ActiveAlert } from '@/types/api';
import { asString } from '@/utils/coerce';
import { formatAircraftLabel, NOT_REPORTED } from '@/utils/format';
import {
  presentAlertState,
  presentRecommendationAction,
  presentRiskLevel,
} from '@/utils/status';

import { alertIdOf } from './alertList';
import styles from './AircraftAlertChooser.module.css';

export interface AircraftAlertChooserProps {
  aircraftId: string;
  callsign: string | null;
  items: readonly ActiveAlert[];
  selectedAlertId: string | null;
  onSelect: (alertId: string) => void;
  onDismiss: () => void;
}

/**
 * Compact chooser for a map-selected aircraft that has several current
 * loaded alerts. Selection is by `alert_id` only.
 */
export function AircraftAlertChooser({
  aircraftId,
  callsign,
  items,
  selectedAlertId,
  onSelect,
  onDismiss,
}: AircraftAlertChooserProps) {
  return (
    <div
      className={styles.chooser}
      role="region"
      aria-label="Current alerts for this aircraft"
    >
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>Current alerts</h2>
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
          const alertId = alertIdOf(item);
          const hazardId = asString(item.hazard_id) ?? NOT_REPORTED;
          const selected = alertId !== null && alertId === selectedAlertId;
          const summary =
            asString(item.message) ??
            presentRecommendationAction(item.primary_action_type).label;

          return (
            <li key={alertId ?? hazardId}>
              <button
                type="button"
                className={`${styles.row} ${selected ? styles.rowSelected : ''}`}
                disabled={alertId === null}
                aria-current={selected ? 'true' : undefined}
                onClick={() => {
                  if (alertId !== null) {
                    onSelect(alertId);
                  }
                }}
              >
                <span className={`${styles.hazard} wv-numeric`}>{hazardId}</span>
                <StatusPill
                  size="sm"
                  presentation={presentAlertState(item.alert_state)}
                />
                <StatusPill
                  size="sm"
                  presentation={presentRiskLevel(item.risk_level)}
                />
                <span className={styles.type}>{summary}</span>
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
