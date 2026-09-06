import { Link } from 'react-router-dom';

import type { ContextSelection } from '@/features/aircraft/investigation';
import { aircraftInvestigationPath } from '@/features/aircraft/investigation';
import type { MapAircraft } from '@/features/map/aircraftGeoJson';
import { formatAircraftLabel } from '@/utils/format';

import styles from './SelectedAircraftStrip.module.css';

export interface SelectedAircraftStripProps {
  selection: ContextSelection;
  aircraft: MapAircraft | null;
  hasProjection: boolean;
  onClear: () => void;
}

/**
 * Overview keeps map focus and the projection path. Full investigation lives
 * on the Aircraft page.
 */
export function SelectedAircraftStrip({
  selection,
  aircraft,
  hasProjection,
  onClear,
}: SelectedAircraftStripProps) {
  return (
    <div className={styles.strip}>
      <div className={styles.identity}>
        <span className={styles.callsign}>
          {formatAircraftLabel(aircraft?.callsign, selection.aircraftId)}
        </span>
        <span className={`${styles.id} wv-numeric`}>
          {selection.aircraftId.toUpperCase()}
        </span>
      </div>

      <p className={styles.note}>
        {hasProjection
          ? 'Current projection shown on the map.'
          : 'No current projection was returned for this aircraft.'}
      </p>

      <Link className={styles.open} to={aircraftInvestigationPath(selection)}>
        Open investigation
      </Link>

      <button type="button" className={styles.clear} onClick={onClear}>
        Clear
      </button>
    </div>
  );
}
