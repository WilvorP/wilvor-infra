import { Link } from 'react-router-dom';

import { DataField, DataFieldGrid } from '@/components/DataField';
import { StatusPill } from '@/components/StatusPill';
import {
  aircraftInvestigationPath,
  matchCurrentContext,
  type ContextSelection,
} from '@/features/aircraft/investigation';
import type { ActiveAlert, AircraftDetailResponse } from '@/types/api';
import { asNumber, asString } from '@/utils/coerce';
import {
  formatAircraftLabel,
  formatCount,
  formatRiskScore,
  formatUtcDateTime,
  humaniseToken,
  NOT_REPORTED,
} from '@/utils/format';
import {
  presentAlertState,
  presentRecommendationAction,
  presentRiskLevel,
} from '@/utils/status';

import { selectionFromAlert } from './alertList';
import styles from './SelectedAlertStrip.module.css';

export type SelectedAlertPresence =
  | 'current'
  | 'resolved'
  | 'unloaded'
  | 'missing-aircraft'
  | 'missing-hazard';

export interface SelectedAlertStripProps {
  alertId: string;
  item: ActiveAlert | null;
  callsign: string | null;
  presence: SelectedAlertPresence;
  detail?: AircraftDetailResponse | null;
  detailFailed?: boolean;
  detailPending?: boolean;
  mapAircraftMissing?: boolean;
  onClear: () => void;
}

const UNAVAILABLE = 'Unavailable';

/**
 * Full selected-alert detail for one `alert_id`.
 *
 * Alert fields come from the `/alerts/active` row. Linked encounter, risk,
 * recommendation and projection come only from an exact `currentContext`
 * match on the already-fetched aircraft detail.
 */
export function SelectedAlertStrip({
  alertId,
  item,
  callsign,
  presence,
  detail = null,
  detailFailed = false,
  detailPending = false,
  mapAircraftMissing = false,
  onClear,
}: SelectedAlertStripProps) {
  const selection = item ? selectionFromAlert(item) : null;
  const aircraftId = asString(item?.aircraft_id);
  const message = asString(item?.message);
  const matchedContext =
    selection && detail
      ? matchCurrentContext(detail.currentContexts, selection)
      : null;
  const investigationSelection = investigationSelectionFromMatch(
    selection,
    matchedContext?.encounter?.encounter_id,
  );
  const projectionPoints = detail?.projectionPoints;
  const projectionShown =
    presence === 'current' &&
    matchedContext !== null &&
    projectionPoints != null &&
    projectionPoints.length > 0;

  return (
    <section className={styles.panel} aria-label="Selected alert">
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>Selected alert</h2>
          <p className={styles.subtitle}>
            Stored `/alerts/active` record for this `alert_id`. Linked context
            is the exact currentContext match only.
          </p>
        </div>
        <div className={styles.actions}>
          {investigationSelection ? (
            <Link
              className={styles.open}
              to={aircraftInvestigationPath(investigationSelection)}
            >
              Open Aircraft Investigation
            </Link>
          ) : (
            <p className={styles.note}>
              This current alert has no aircraft id, so it cannot open
              investigation.
            </p>
          )}
          <button type="button" className={styles.clear} onClick={onClear}>
            Clear
          </button>
        </div>
      </header>

      {presence === 'resolved' ? (
        <p className={styles.warning}>This alert is no longer current.</p>
      ) : null}
      {presence === 'unloaded' ? (
        <p className={styles.note}>This alert is not among the loaded pages.</p>
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

      <div className={styles.sections}>
        <section className={styles.section}>
          <h3 className={styles.heading}>Alert</h3>
          {item ? (
            <>
              <StatusPill
                size="sm"
                presentation={presentAlertState(item.alert_state)}
              />
              <p className={styles.message}>{message ?? NOT_REPORTED}</p>
              <DataFieldGrid columns={2}>
                <DataField
                  label="Alert ID"
                  value={asString(item.alert_id) ?? alertId}
                  numeric
                />
                <DataField
                  label="Fingerprint"
                  value={asString(item.fingerprint)}
                  numeric
                />
                <DataField
                  label="Type"
                  value={humaniseToken(item.alert_type)}
                />
                <DataField
                  label="State reason"
                  value={asString(item.state_reason)}
                />
                <DataField
                  label="Aircraft"
                  value={formatAircraftLabel(callsign, aircraftId)}
                />
                <DataField
                  label="Aircraft ID"
                  value={aircraftId?.toUpperCase()}
                  numeric
                />
                <DataField
                  label="Callsign"
                  value={asString(callsign)}
                />
                <DataField
                  label="Hazard ID"
                  value={asString(item.hazard_id)}
                  numeric
                />
              </DataFieldGrid>
            </>
          ) : (
            <DataFieldGrid columns={1}>
              <DataField label="Alert ID" value={alertId} numeric />
            </DataFieldGrid>
          )}
        </section>

        <section className={styles.section}>
          <h3 className={styles.heading}>Supporting Risk</h3>
          {item ? (
            <>
              <StatusPill
                size="sm"
                presentation={presentRiskLevel(item.risk_level)}
              />
              <DataFieldGrid columns={1}>
                <DataField
                  label="Risk ID"
                  value={asString(item.risk_id)}
                  numeric
                />
                <DataField
                  label="Score"
                  value={
                    asNumber(item.risk_score) === null
                      ? NOT_REPORTED
                      : `${formatRiskScore(item.risk_score)}/100`
                  }
                  numeric
                />
              </DataFieldGrid>
            </>
          ) : (
            <p className={styles.note}>{UNAVAILABLE}</p>
          )}
        </section>

        <section className={styles.section}>
          <h3 className={styles.heading}>Recommendation</h3>
          {item ? (
            <>
              <StatusPill
                size="sm"
                presentation={presentRecommendationAction(
                  item.primary_action_type,
                )}
              />
              <DataFieldGrid columns={1}>
                <DataField
                  label="Recommendation ID"
                  value={asString(item.recommendation_id)}
                  numeric
                />
                <DataField
                  label="Preferred airport"
                  value={asString(item.preferred_airport_id)}
                  numeric
                />
              </DataFieldGrid>
            </>
          ) : (
            <p className={styles.note}>{UNAVAILABLE}</p>
          )}
        </section>

        <section className={styles.section}>
          <h3 className={styles.heading}>Lifecycle</h3>
          {item ? (
            <DataFieldGrid columns={1}>
              <DataField
                label="Created"
                value={formatUtcDateTime(item.created_at_utc)}
                numeric
              />
              <DataField
                label="Updated"
                value={formatUtcDateTime(item.updated_at_utc)}
                numeric
              />
              <DataField
                label="Valid until"
                value={formatUtcDateTime(item.valid_until_utc)}
                numeric
              />
              <DataField
                label="Last notified"
                value={formatUtcDateTime(item.last_notified_at_utc)}
                numeric
              />
              <DataField
                label="Notifications"
                value={
                  asNumber(item.notification_count) === null
                    ? NOT_REPORTED
                    : formatCount(item.notification_count)
                }
                numeric
              />
            </DataFieldGrid>
          ) : (
            <p className={styles.note}>{UNAVAILABLE}</p>
          )}
        </section>

        <section className={styles.section}>
          <h3 className={styles.heading}>Linked Operational Context</h3>
          <LinkedOperationalContext
            presence={presence}
            detailPending={detailPending}
            detailFailed={detailFailed}
            matched={matchedContext !== null}
            projectionShown={projectionShown}
            projectionCount={projectionPoints?.length ?? null}
            projectionId={asString(detail?.projection?.projection_id)}
            encounterId={asString(matchedContext?.encounter?.encounter_id)}
            hazardId={asString(matchedContext?.encounter?.hazard_id)}
            riskId={asString(matchedContext?.risk?.risk_id)}
            recommendationId={asString(
              matchedContext?.recommendation?.recommendation_id,
            )}
            alertId={asString(matchedContext?.alert?.alert_id)}
          />
        </section>
      </div>
    </section>
  );
}

function investigationSelectionFromMatch(
  selection: ContextSelection | null,
  encounterId: unknown,
): ContextSelection | null {
  if (selection === null) {
    return null;
  }

  const matchedEncounterId = asString(encounterId);

  if (matchedEncounterId === null) {
    return selection;
  }

  return { ...selection, encounterId: matchedEncounterId };
}

function LinkedOperationalContext({
  presence,
  detailPending,
  detailFailed,
  matched,
  projectionShown,
  projectionCount,
  projectionId,
  encounterId,
  hazardId,
  riskId,
  recommendationId,
  alertId,
}: {
  presence: SelectedAlertPresence;
  detailPending: boolean;
  detailFailed: boolean;
  matched: boolean;
  projectionShown: boolean;
  projectionCount: number | null;
  projectionId: string | null;
  encounterId: string | null;
  hazardId: string | null;
  riskId: string | null;
  recommendationId: string | null;
  alertId: string | null;
}) {
  if (presence === 'resolved' || presence === 'unloaded') {
    return <p className={styles.note}>{UNAVAILABLE}</p>;
  }

  if (detailPending) {
    return <p className={styles.note}>Loading linked operational context.</p>;
  }

  if (detailFailed) {
    return <p className={styles.note}>Aircraft detail unavailable.</p>;
  }

  if (!matched) {
    return (
      <p className={styles.note}>
        No exact currentContext match. {UNAVAILABLE}
      </p>
    );
  }

  return (
    <>
      <p className={styles.note}>
        {projectionShown
          ? 'Exact currentContext match. Selected projection shown on the map.'
          : 'Exact currentContext match.'}
      </p>
      <DataFieldGrid columns={1}>
        <DataField label="Encounter ID" value={encounterId} numeric />
        <DataField label="Context hazard" value={hazardId} numeric />
        <DataField label="Context risk ID" value={riskId} numeric />
        <DataField
          label="Context recommendation ID"
          value={recommendationId}
          numeric
        />
        <DataField label="Context alert ID" value={alertId} numeric />
        <DataField label="Projection ID" value={projectionId} numeric />
        <DataField
          label="Projection points"
          value={
            projectionCount === null ? NOT_REPORTED : formatCount(projectionCount)
          }
          numeric
        />
      </DataFieldGrid>
    </>
  );
}
