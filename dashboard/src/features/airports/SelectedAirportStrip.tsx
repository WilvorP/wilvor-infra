import { Link } from 'react-router-dom';

import { StatusPill } from '@/components/StatusPill';
import type { AirportStatus } from '@/types/api';
import { asString } from '@/utils/coerce';
import { presentRiskLevel, presentWeatherImpact } from '@/utils/status';

import styles from './SelectedAirportStrip.module.css';

export interface SelectedAirportStripProps {
  airportId: string;
  airport: AirportStatus | null;
  onClear: () => void;
}

export function SelectedAirportStrip({
  airportId,
  airport,
  onClear,
}: SelectedAirportStripProps) {
  const name = asString(airport?.station_name);

  return (
    <div className={styles.strip}>
      <div className={styles.identity}>
        <span className={styles.icao}>{airportId}</span>
        {name ? <span className={styles.name}>{name}</span> : null}
      </div>

      {airport ? (
        <>
          <StatusPill
            size="sm"
            presentation={presentWeatherImpact(airport.weather_impact_status)}
          />
          <StatusPill
            size="sm"
            prefix="Wx risk"
            presentation={presentRiskLevel(airport.weather_risk_level)}
          />
        </>
      ) : (
        <p className={styles.note}>
          This airport is not in the loaded pages. Open investigation to load
          the stored record.
        </p>
      )}

      <Link
        className={styles.open}
        to={`/airports/${encodeURIComponent(airportId)}`}
      >
        Open investigation
      </Link>

      <button type="button" className={styles.clear} onClick={onClear}>
        Clear
      </button>
    </div>
  );
}
