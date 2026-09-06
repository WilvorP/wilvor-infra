import { Link } from 'react-router-dom';

import { StatusPill } from '@/components/StatusPill';
import { aircraftInvestigationPath } from '@/features/aircraft/investigation';
import type { AircraftDetailResponse, Recommendation } from '@/types/api';
import { asString, asStringArray } from '@/utils/coerce';
import {
  formatAircraftLabel,
  formatRiskScore,
  humaniseToken,
  NOT_REPORTED,
} from '@/utils/format';
import {
  presentConfidence,
  presentRecommendationAction,
  presentRiskLevel,
} from '@/utils/status';

import {
  presentAirportEvidence,
  selectionFromRecommendation,
} from './recommendationList';
import styles from './SelectedRecommendationStrip.module.css';

export type SelectedRecommendationPresence =
  | 'current'
  | 'resolved'
  | 'unloaded'
  | 'missing-aircraft'
  | 'missing-hazard';

export interface SelectedRecommendationStripProps {
  recommendationId: string;
  item: Recommendation | null;
  callsign: string | null;
  presence: SelectedRecommendationPresence;
  detail?: AircraftDetailResponse | null;
  detailFailed?: boolean;
  mapAircraftMissing?: boolean;
  onClear: () => void;
}

/**
 * Compact selected-recommendation context. Full investigation lives on
 * `/aircraft`. Reasons and limitations come from the list item, not a
 * frontend rationale.
 */
export function SelectedRecommendationStrip({
  recommendationId,
  item,
  callsign,
  presence,
  detail = null,
  detailFailed = false,
  mapAircraftMissing = false,
  onClear,
}: SelectedRecommendationStripProps) {
  const selection = item ? selectionFromRecommendation(item) : null;
  const reasons = asStringArray(item?.reasons);
  const limitations = asStringArray(item?.limitations);
  const aircraftId = asString(item?.aircraft_id);
  const hazardId = asString(item?.hazard_id);
  const advisory = asString(item?.primary_action_details?.advisory);
  const actionLabel = presentRecommendationAction(item?.primary_action_type).label;
  const airportEvidence = item ? presentAirportEvidence(item) : { kind: 'none' as const };
  const matchedContext =
    selection && detail
      ? detail.currentContexts?.some(
          (context) =>
            asString(context.recommendation?.recommendation_id) ===
              asString(item?.recommendation_id) ||
            asString(context.risk?.risk_id) === asString(item?.risk_id),
        )
      : false;
  const projectionShown =
    presence === 'current' &&
    detail?.projectionPoints != null &&
    detail.projectionPoints.length > 0;

  return (
    <div className={styles.strip}>
      <section className={styles.block}>
        <h2 className={styles.heading}>Primary advisory</h2>
        {item ? (
          <>
            <p className={styles.identity}>
              <span className={styles.callsign}>{actionLabel}</span>
            </p>
            {advisory ? <p className={styles.line}>{advisory}</p> : null}
            <StatusPill
              size="sm"
              presentation={presentRecommendationAction(item.primary_action_type)}
            />
            <p className={styles.note}>{humaniseToken(item.recommendation_status)}</p>
          </>
        ) : (
          <p className={styles.note}>
            Recommendation <span className="wv-numeric">{recommendationId}</span>
          </p>
        )}
      </section>

      {item ? (
        <section className={styles.block}>
          <h2 className={styles.heading}>Aircraft</h2>
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
        </section>
      ) : null}

      {item ? (
        <section className={styles.block}>
          <h2 className={styles.heading}>Supporting risk</h2>
          <StatusPill
            size="sm"
            presentation={presentRiskLevel(item.risk_level)}
          />
          <p className={styles.line}>
            Score{' '}
            <span className="wv-numeric">{formatRiskScore(item.risk_score)}</span>
            /100
          </p>
          <StatusPill
            size="sm"
            prefix="Confidence"
            presentation={presentConfidence(item.confidence)}
          />
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
            <p className={styles.note}>No stored reasons on this recommendation.</p>
          )}
        </section>
      ) : null}

      {item ? (
        <section className={styles.block}>
          <h2 className={styles.heading}>Uncertainties / limitations</h2>
          {limitations.length > 0 ? (
            <ul className={styles.reasons}>
              {limitations.map((limitation) => (
                <li key={limitation}>{limitation}</li>
              ))}
            </ul>
          ) : (
            <p className={styles.note}>
              No stored limitations on this recommendation.
            </p>
          )}
          {airportEvidence.kind === 'preferred' ? (
            <p className={styles.line}>
              Stored preferred airport{' '}
              <span className="wv-numeric">{airportEvidence.airportId}</span>
            </p>
          ) : null}
          {airportEvidence.kind === 'limitation' ? (
            <p className={styles.note}>{airportEvidence.reason}</p>
          ) : null}
          {airportEvidence.kind === 'unavailable' ? (
            <p className={styles.note}>Airport evidence unavailable</p>
          ) : null}
        </section>
      ) : null}

      <section className={`${styles.block} ${styles.action}`}>
        <h2 className={styles.heading}>Action</h2>
        {presence === 'resolved' ? (
          <p className={styles.warning}>
            This recommendation is no longer current.
          </p>
        ) : null}
        {presence === 'unloaded' ? (
          <p className={styles.note}>
            This recommendation is not among the loaded pages.
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
        ) : matchedContext && projectionShown ? (
          <p className={styles.note}>Selected projection shown on the map.</p>
        ) : presence === 'current' && asString(item?.risk_id) ? (
          <p className={styles.note}>
            Risk <span className="wv-numeric">{asString(item?.risk_id)}</span>
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
            This current recommendation has no aircraft id, so it cannot open
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
