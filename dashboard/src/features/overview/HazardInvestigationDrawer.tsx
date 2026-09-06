import { DataField, DataFieldGrid } from '@/components/DataField';
import { EmptyState } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import type { ActiveHazard } from '@/types/api';
import { asNumber, asString } from '@/utils/coerce';
import {
  formatNumber,
  formatUtcDateTime,
  humaniseToken,
  NOT_REPORTED,
} from '@/utils/format';
import { presentHazardSeverity } from '@/utils/status';

import styles from './HazardInvestigationDrawer.module.css';

export interface HazardInvestigationDrawerProps {
  hazard: ActiveHazard | null;
  onClose: () => void;
}

/**
 * Contextual investigation surface for a map selection.
 *
 * Every field is read directly from the `ActiveHazards` record returned by
 * `GET /hazards/active`. Attributes the pipeline does not persist — notably a
 * bounding box, a decoded phenomenon name, and an issuing-office name (only
 * the ICAO identifier `source_icao_id` exists) — are not shown rather than
 * being reconstructed.
 */
export function HazardInvestigationDrawer({
  hazard,
  onClose,
}: HazardInvestigationDrawerProps) {
  if (hazard === null) {
    return (
      <div className={styles.drawer}>
        <EmptyState
          title="No selection"
          detail="Select a hazard on the map to inspect its validity window, altitude band and source product."
        />
      </div>
    );
  }

  const hazardId = asString(hazard.hazard_id) ?? NOT_REPORTED;
  const rawText = asString(hazard.raw_text);
  const minAltitude = asNumber(hazard.minimum_lower_altitude_ft);
  const maxAltitude = asNumber(hazard.maximum_upper_altitude_ft);

  const altitudeBand =
    minAltitude === null && maxAltitude === null
      ? NOT_REPORTED
      : `${minAltitude === null ? '—' : formatNumber(minAltitude)} – ` +
        `${maxAltitude === null ? '—' : formatNumber(maxAltitude)} ft`;

  return (
    <div className={styles.drawer}>
      <div className={styles.header}>
        <div className={styles.identity}>
          <span className={styles.kind}>
            {humaniseToken(hazard.hazard_type)}
          </span>
          <span className={`${styles.id} wv-numeric`}>{hazardId}</span>
        </div>

        <StatusPill
          size="sm"
          prefix="Severity"
          presentation={presentHazardSeverity(hazard.severity)}
        />

        <button type="button" className={styles.close} onClick={onClose}>
          Close
        </button>
      </div>

      <div className={styles.body}>
        <DataFieldGrid columns={3}>
          <DataField
            label="Product"
            value={asString(hazard.product_type) ?? NOT_REPORTED}
          />
          <DataField
            label="Issuing office (ICAO)"
            value={asString(hazard.source_icao_id) ?? NOT_REPORTED}
          />
          <DataField
            label="Amendment"
            value={humaniseToken(hazard.amendment_type)}
          />

          <DataField
            label="Valid from"
            value={formatUtcDateTime(hazard.valid_from_utc)}
            numeric
          />
          <DataField
            label="Valid to"
            value={formatUtcDateTime(hazard.valid_to_utc)}
            numeric
          />
          <DataField label="Altitude band" value={altitudeBand} numeric />

          <DataField
            label="Movement"
            value={
              asNumber(hazard.movement_direction_deg) === null &&
              asNumber(hazard.movement_speed_kt) === null
                ? NOT_REPORTED
                : `${formatNumber(hazard.movement_direction_deg)}° at ` +
                  `${formatNumber(hazard.movement_speed_kt)} kt`
            }
            numeric
          />
          <DataField
            label="Geometry"
            value={`${humaniseToken(hazard.geometry_type)} · ${formatNumber(
              hazard.geometry_point_count,
            )} pts`}
            numeric
          />
          <DataField
            label="Materialised"
            value={formatUtcDateTime(hazard.materialized_at_utc)}
            numeric
          />
        </DataFieldGrid>

        {rawText !== null ? (
          <div className={styles.rawBlock}>
            <h3 className={styles.rawHeading}>Source text</h3>
            <pre className={styles.raw}>{rawText}</pre>
          </div>
        ) : null}
      </div>
    </div>
  );
}
