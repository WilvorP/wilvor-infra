import { Link } from 'react-router-dom';

import { DataField, DataFieldGrid } from '@/components/DataField';
import type { ContextSelection } from '@/features/aircraft/investigation';
import { aircraftInvestigationPath } from '@/features/aircraft/investigation';
import type { MapAircraft } from '@/features/map/aircraftGeoJson';
import {
  formatAircraftLabel,
  formatNumber,
  formatUtcTime,
  NOT_REPORTED,
} from '@/utils/format';

import styles from './Inspector.module.css';

export interface SelectedAircraftStripProps {
  selection: ContextSelection;
  aircraft: MapAircraft | null;
  hasProjection: boolean;
  onClear: () => void;
}

/**
 * Docked inspector for the selected aircraft.
 *
 * Shows only what `/map/aircraft` already carries for this aircraft. Overview
 * keeps map focus and the projection path; full investigation lives on the
 * Aircraft page.
 */
export function SelectedAircraftStrip({
  selection,
  aircraft,
  hasProjection,
  onClear,
}: SelectedAircraftStripProps) {
  const positionTime =
    aircraft?.positionTimeEpoch != null
      ? formatUtcTime(new Date(aircraft.positionTimeEpoch * 1000).toISOString())
      : NOT_REPORTED;

  return (
    <section className={styles.inspector} aria-label="Selected aircraft">
      <header className={styles.header}>
        <span className={styles.kicker}>Selected aircraft</span>
        <button type="button" className={styles.close} onClick={onClear}>
          Clear
        </button>
      </header>

      <div className={styles.identity}>
        <span className={styles.name}>
          {formatAircraftLabel(aircraft?.callsign, selection.aircraftId)}
        </span>
        <span className={`${styles.id} wv-mono`}>
          {selection.aircraftId.toUpperCase()}
        </span>
      </div>

      <div className={styles.body}>
        {aircraft === null ? (
          <p className={styles.warning}>
            Not present in the loaded aircraft feed.
          </p>
        ) : null}

        <DataFieldGrid columns={2}>
          <DataField
            label="Altitude (baro)"
            value={formatNumber(aircraft?.baroAltitudeFt, { unit: 'ft' })}
            numeric
          />
          <DataField
            label="Ground speed"
            value={formatNumber(aircraft?.groundSpeedKt, { unit: 'kt' })}
            numeric
          />
          <DataField
            label="Track"
            value={
              aircraft?.trackDeg == null
                ? NOT_REPORTED
                : `${formatNumber(aircraft.trackDeg)}°`
            }
            numeric
          />
          <DataField label="Position time" value={positionTime} numeric />
        </DataFieldGrid>

        <p className={styles.note}>
          {hasProjection
            ? 'Current projection shown on the map.'
            : 'No current projection was returned for this aircraft.'}
        </p>

        <Link className={styles.primaryAction} to={aircraftInvestigationPath(selection)}>
          Open investigation
        </Link>
      </div>
    </section>
  );
}
