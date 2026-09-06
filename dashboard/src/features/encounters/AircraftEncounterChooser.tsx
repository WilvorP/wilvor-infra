import { StatusPill } from '@/components/StatusPill';
import type { ActiveEncounterItem } from '@/types/api';
import { asString } from '@/utils/coerce';
import { formatAircraftLabel, humaniseToken, NOT_REPORTED } from '@/utils/format';
import { presentEncounterState, presentRiskLevel } from '@/utils/status';

import { encounterIdOf } from './encounterList';
import styles from './AircraftEncounterChooser.module.css';

export interface AircraftEncounterChooserProps {
  aircraftId: string;
  callsign: string | null;
  items: readonly ActiveEncounterItem[];
  selectedEncounterId: string | null;
  onSelect: (encounterId: string) => void;
  onDismiss: () => void;
}

/**
 * Compact chooser for a map-selected aircraft that has several current
 * loaded encounters. Selection is by `encounter_id` only.
 */
export function AircraftEncounterChooser({
  aircraftId,
  callsign,
  items,
  selectedEncounterId,
  onSelect,
  onDismiss,
}: AircraftEncounterChooserProps) {
  return (
    <div
      className={styles.chooser}
      role="region"
      aria-label="Current encounters for this aircraft"
    >
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>Current encounters</h2>
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
          const encounterId = encounterIdOf(item);
          const hazardId = asString(item.encounter?.hazard_id) ?? NOT_REPORTED;
          const selected =
            encounterId !== null && encounterId === selectedEncounterId;

          return (
            <li key={encounterId ?? hazardId}>
              <button
                type="button"
                className={`${styles.row} ${selected ? styles.rowSelected : ''}`}
                disabled={encounterId === null}
                aria-current={selected ? 'true' : undefined}
                onClick={() => {
                  if (encounterId !== null) {
                    onSelect(encounterId);
                  }
                }}
              >
                <span className={`${styles.hazard} wv-numeric`}>{hazardId}</span>
                <span className={styles.type}>
                  {humaniseToken(item.encounter?.hazard_type)}
                </span>
                <StatusPill
                  size="sm"
                  presentation={presentRiskLevel(item.risk?.risk_level)}
                />
                <StatusPill
                  size="sm"
                  presentation={presentEncounterState(
                    item.encounter?.encounter_state,
                  )}
                />
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
