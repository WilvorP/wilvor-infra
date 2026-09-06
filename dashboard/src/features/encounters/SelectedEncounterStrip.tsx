import { Link } from 'react-router-dom';

import { StatusPill } from '@/components/StatusPill';
import { aircraftInvestigationPath } from '@/features/aircraft/investigation';
import type { AircraftDetailResponse } from '@/types/api';
import type { ActiveEncounterItem } from '@/types/api';
import { asString, asStringArray } from '@/utils/coerce';
import {
  formatAircraftLabel,
  formatRiskScore,
  NOT_REPORTED,
} from '@/utils/format';
import {
  presentConfidence,
  presentEncounterState,
  presentInsideNow,
  presentOverlapStatus,
  presentRiskLevel,
} from '@/utils/status';

import { selectionFromEncounter } from './encounterList';
import styles from './SelectedEncounterStrip.module.css';

export type SelectedEncounterPresence =
  | 'current'
  | 'resolved'
  | 'unloaded'
  | 'missing-aircraft'
  | 'missing-hazard';

export interface SelectedEncounterStripProps {
  encounterId: string;
  item: ActiveEncounterItem | null;
  callsign: string | null;
  presence: SelectedEncounterPresence;
  detail?: AircraftDetailResponse | null;
  detailFailed?: boolean;
  mapAircraftMissing?: boolean;
  onClear: () => void;
}

/**
 * Compact selected-encounter context. Full investigation lives on `/aircraft`.
 */
export function SelectedEncounterStrip({
  encounterId,
  item,
  callsign,
  presence,
  detail = null,
  detailFailed = false,
  mapAircraftMissing = false,
  onClear,
}: SelectedEncounterStripProps) {
  const selection = item ? selectionFromEncounter(item) : null;
  const encounter = item?.encounter;
  const risk = item?.risk;
  const reasons = asStringArray(risk?.reasons);
  const aircraftId = asString(encounter?.aircraft_id);
  const hazardId = asString(encounter?.hazard_id);
  const projectionId = asString(encounter?.projection_id);
  const selectedProjectionId = asString(detail?.projection?.projection_id);
  const matchingProjection =
    projectionId !== null &&
    selectedProjectionId !== null &&
    projectionId === selectedProjectionId;

  return (
    <div className={styles.strip}>
      <section className={styles.block}>
        <h2 className={styles.heading}>Encounter</h2>
        {item ? (
          <>
            <p className={styles.identity}>
              <span className={styles.callsign}>
                {formatAircraftLabel(callsign, aircraftId)}
              </span>
              {aircraftId ? (
                <span className={`${styles.muted} wv-numeric`}>
                  {aircraftId.toUpperCase()}
                </span>
              ) : null}
            </p>
            <p className={styles.line}>
              Hazard{' '}
              <span className="wv-numeric">{hazardId ?? NOT_REPORTED}</span>
            </p>
            <StatusPill
              size="sm"
              presentation={presentEncounterState(encounter?.encounter_state)}
            />
            <StatusPill
              size="sm"
              presentation={presentInsideNow(encounter?.inside_now)}
            />
          </>
        ) : (
          <p className={styles.note}>
            Encounter <span className="wv-numeric">{encounterId}</span>
          </p>
        )}
      </section>

      {item ? (
        <section className={styles.block}>
          <h2 className={styles.heading}>Risk</h2>
          <StatusPill
            size="sm"
            presentation={presentRiskLevel(risk?.risk_level)}
          />
          <p className={styles.line}>
            Score{' '}
            <span className="wv-numeric">{formatRiskScore(risk?.risk_score)}</span>
            /100
          </p>
          <StatusPill
            size="sm"
            prefix="Confidence"
            presentation={presentConfidence(risk?.confidence)}
          />
        </section>
      ) : null}

      {item ? (
        <section className={styles.block}>
          <h2 className={styles.heading}>Evidence</h2>
          <p className={styles.line}>
            Geometry {presentOverlapStatus(encounter?.geometry_overlap_status).label}
          </p>
          <p className={styles.line}>
            Time {presentOverlapStatus(encounter?.time_overlap_status).label}
          </p>
          <p className={styles.line}>
            Altitude {presentOverlapStatus(encounter?.altitude_overlap_status).label}
          </p>
        </section>
      ) : null}

      {item ? (
        <section className={styles.block}>
          <h2 className={styles.heading}>Why</h2>
          {reasons.length > 0 ? (
            <ul className={styles.reasons}>
              {reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : (
            <p className={styles.note}>No stored reasons on this risk.</p>
          )}
        </section>
      ) : null}

      <section className={`${styles.block} ${styles.action}`}>
        <h2 className={styles.heading}>Action</h2>
        {presence === 'resolved' ? (
          <p className={styles.warning}>This encounter is no longer current.</p>
        ) : null}
        {presence === 'unloaded' ? (
          <p className={styles.note}>
            This encounter is not among the loaded pages.
          </p>
        ) : null}
        {mapAircraftMissing && presence === 'current' ? (
          <p className={styles.note}>
            This aircraft is not in the current map feed.
          </p>
        ) : null}
        {presence === 'missing-hazard' ? (
          <p className={styles.note}>
            This hazard is not in the active hazard layer.
          </p>
        ) : null}
        {detailFailed ? (
          <p className={styles.note}>
            Aircraft detail could not be loaded for the selected projection.
          </p>
        ) : matchingProjection ? (
          <p className={styles.note}>Selected projection shown on the map.</p>
        ) : presence === 'current' && projectionId ? (
          <p className={styles.note}>
            Projection <span className="wv-numeric">{projectionId}</span>
          </p>
        ) : null}

        {selection ? (
          <Link
            className={styles.open}
            to={aircraftInvestigationPath(selection)}
          >
            Open Aircraft Investigation
          </Link>
        ) : (
          <p className={styles.note}>
            This current encounter has no aircraft id, so it cannot open
            investigation.
          </p>
        )}

        <button type="button" className={styles.clear} onClick={onClear}>
          Clear
        </button>
      </section>
    </div>
  );
}
