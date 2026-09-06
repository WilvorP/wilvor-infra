import { useEffect, useMemo, useState, type ReactNode } from 'react';

import { describeApiError, isApiError } from '@/api/errors';
import { DataField, DataFieldGrid } from '@/components/DataField';
import { Notice } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import type { MapAircraft } from '@/features/map/aircraftGeoJson';
import { useAircraftDetail } from '@/hooks/useOperationalQueries';
import type {
  ActiveAlert,
  AircraftCurrentState,
  AircraftHazardEncounter,
  AircraftOperationalContext,
  AircraftProjection,
  AircraftProjectionPoint,
  Recommendation,
  RiskResult,
} from '@/types/api';
import { asNumber, asString, asStringArray } from '@/utils/coerce';
import {
  formatAge,
  formatAircraftLabel,
  formatBoolean,
  formatNumber,
  formatRiskScore,
  formatUtcDateTime,
  humaniseToken,
  NOT_REPORTED,
  secondsSince,
} from '@/utils/format';
import {
  presentAlertState,
  presentConfidence,
  presentFreshness,
  presentHazardSeverity,
  presentOverlapStatus,
  presentRiskLevel,
} from '@/utils/status';

import {
  asRecordList,
  contextSelectionIsExplicit,
  formatAdvisoryAction,
  formatEvidenceReference,
  formatSourceVersions,
  highestCurrentRisk,
  latestRecommendation,
  latestRisk,
  matchCurrentContext,
  uniqueHorizons,
  type ContextSelection,
} from './investigation';
import styles from './AircraftSelectionPanel.module.css';

export interface AircraftSelectionPanelProps {
  /** The selection itself, which outlives any single refresh. */
  aircraftId: string;
  /** Resolved from the latest poll, or `null` if it dropped out of the feed. */
  aircraft: MapAircraft | null;
  onClose: () => void;
  /**
   * Explicit current-context IDs from the worklist. Matched by stored IDs,
   * never by latest timestamp.
   */
  contextSelection?: ContextSelection | null;
  /** Clock source, injectable so age rendering is testable. */
  now?: number;
  /**
   * `standalone` keeps a self-contained header for isolated tests.
   * `page` is the dedicated investigation surface; page chrome owns identity.
   */
  variant?: 'standalone' | 'page';
  /** Investigation map, rendered in the spatial column. */
  children?: ReactNode;
}

/**
 * Operational investigation surface for a selected aircraft.
 *
 * Map-level fields stay visible while `GET /aircraft/{aircraftId}` loads.
 * Risk, recommendation and overlap values are presented as stored — never
 * recalculated here.
 */
export function AircraftSelectionPanel({
  aircraftId,
  aircraft,
  onClose,
  contextSelection = null,
  now = Date.now(),
  variant = 'standalone',
  children,
}: AircraftSelectionPanelProps) {
  const detailQuery = useAircraftDetail(aircraftId);
  const detail = detailQuery.data;
  const detailPending = detailQuery.isPending && detail === undefined;
  const detailFailed = detailQuery.isError && detail === undefined;
  const detailStale = detailQuery.isError && detail !== undefined;
  const notFound =
    detailQuery.isError &&
    isApiError(detailQuery.error) &&
    detailQuery.error.kind === 'client' &&
    detailQuery.error.status === 404;

  const current = detail?.aircraft ?? null;
  const callsign = aircraft?.callsign ?? asString(current?.callsign);
  const contexts = asRecordList(detail?.currentContexts);
  const matched = matchCurrentContext(contexts, contextSelection);
  const expectingMatch = contextSelectionIsExplicit(contextSelection);
  const [viewedOverrideKey, setViewedOverrideKey] = useState<string | null>(
    null,
  );

  useEffect(() => {
    setViewedOverrideKey(null);
  }, [
    aircraftId,
    contextSelection?.encounterId,
    contextSelection?.recommendationId,
    contextSelection?.riskId,
    contextSelection?.alertId,
    contextSelection?.fingerprint,
  ]);

  const viewed = useMemo(
    () =>
      resolveViewedContext(
        contexts,
        matched,
        expectingMatch,
        viewedOverrideKey,
      ),
    [contexts, matched, expectingMatch, viewedOverrideKey],
  );

  const header = (
    <div className={styles.header}>
      <div className={styles.identity}>
        <span className={styles.callsign}>
          {formatAircraftLabel(callsign, aircraftId)}
        </span>
        <span className={`${styles.id} wv-numeric`}>
          {aircraftId.toUpperCase()}
        </span>
      </div>

      {highestCurrentRisk(detail?.currentContexts) ? (
        <StatusPill
          size="sm"
          prefix="Risk"
          presentation={presentRiskLevel(
            highestCurrentRisk(detail?.currentContexts)?.risk_level,
          )}
        />
      ) : null}

      <button type="button" className={styles.close} onClick={onClose}>
        Close
      </button>
    </div>
  );

  return (
    <div className={variant === 'page' ? styles.pageRoot : styles.panel}>
      {variant === 'standalone' ? header : null}

      <div className={variant === 'page' ? styles.pageBody : styles.body}>
        {aircraft === null ? (
          <Notice tone="warning">
            This aircraft was not present in the most recent map refresh. It
            may have landed, left coverage, or its current-state record may
            have expired.
          </Notice>
        ) : null}

        {detailPending ? (
          <Notice>Loading investigation context…</Notice>
        ) : null}

        {detailStale ? (
          <Notice tone="warning">
            Showing the last successful investigation refresh. The most recent
            update failed: {describeApiError(detailQuery.error)}
          </Notice>
        ) : null}

        {detailFailed && notFound ? (
          <Notice tone="warning">
            No investigation record exists for this aircraft. Map-level
            position is still shown when available.
          </Notice>
        ) : null}

        {detailFailed && !notFound ? (
          <Notice tone="warning">
            Detailed investigation context is unavailable.{' '}
            {describeApiError(detailQuery.error)}
          </Notice>
        ) : null}

        <div className={styles.layout}>
          <div className={styles.decision}>
            {detail !== undefined && !notFound ? (
              <>
                <CurrentContextSection
                  contexts={contexts}
                  viewed={viewed}
                  matched={matched}
                  expectingMatch={expectingMatch}
                  viewedKey={contextKey(viewed)}
                  onView={setViewedOverrideKey}
                />
                <WhySection context={viewed} />
              </>
            ) : null}
          </div>

          <div className={styles.spatial}>
            {children != null ? (
              <div className={styles.mapSlot}>{children}</div>
            ) : null}

            <IdentitySection
              aircraft={aircraft}
              current={current}
              now={now}
            />

            {detail !== undefined && !notFound ? (
              <ProjectionSection
                projection={detail.projection ?? null}
                points={detail.projectionPoints}
                now={now}
              />
            ) : null}
          </div>

          {detail !== undefined && !notFound ? (
            <div className={styles.history}>
              <details className={styles.historyDetails}>
                <summary className={styles.historySummary}>
                  Recent history
                </summary>
                <div className={styles.historyBody}>
                  <EncounterSection encounters={detail.recentEncounters} />
                  <RiskSection risks={detail.recentRisks} />
                  <RecommendationSection
                    recommendations={detail.recentRecommendations}
                  />
                  <AlertSection alerts={detail.recentAlerts} />
                </div>
              </details>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function contextKey(
  context: AircraftOperationalContext | null | undefined,
  index = 0,
): string {
  if (context == null) {
    return '';
  }

  return (
    asString(context.encounter?.encounter_id) ??
    asString(context.risk?.risk_id) ??
    asString(context.recommendation?.recommendation_id) ??
    asString(context.alert?.alert_id) ??
    asString(context.alert?.fingerprint) ??
    `index:${index}`
  );
}

/**
 * Viewing target for the decision columns.
 *
 * Worklist IDs still go through `matchCurrentContext`. A chip click is an
 * explicit operator choice, not a recency fallback.
 */
function resolveViewedContext(
  items: readonly AircraftOperationalContext[],
  matched: AircraftOperationalContext | null,
  expectingMatch: boolean,
  viewedOverrideKey: string | null,
): AircraftOperationalContext | null {
  if (viewedOverrideKey !== null) {
    const override = items.find(
      (context, index) => contextKey(context, index) === viewedOverrideKey,
    );

    if (override != null) {
      return override;
    }
  }

  if (matched != null) {
    return matched;
  }

  if (expectingMatch) {
    return null;
  }

  return items[0] ?? null;
}

function IdentitySection({
  aircraft,
  current,
  now,
}: {
  aircraft: MapAircraft | null;
  current: AircraftCurrentState | null;
  now: number;
}) {
  const latitude = aircraft?.latitude ?? asNumber(current?.latitude);
  const longitude = aircraft?.longitude ?? asNumber(current?.longitude);
  const track = aircraft?.trackDeg ?? asNumber(current?.track_deg);
  const baroAltitude =
    aircraft?.baroAltitudeFt ?? asNumber(current?.baro_altitude_ft);
  const groundSpeed =
    aircraft?.groundSpeedKt ?? asNumber(current?.ground_speed_kt);

  const positionIso =
    aircraft?.positionTimeEpoch != null
      ? new Date(aircraft.positionTimeEpoch * 1000).toISOString()
      : asString(current?.position_time_utc);

  const ageSeconds =
    aircraft?.positionTimeEpoch != null
      ? Math.max(0, Math.round(now / 1000 - aircraft.positionTimeEpoch))
      : (asNumber(current?.position_age_seconds) ??
        secondsSince(current?.position_time_utc, now));

  return (
    <Section title="Current state">
      <DataFieldGrid columns={3}>
        <DataField
          label="Latitude"
          value={formatNumber(latitude, { digits: 4 })}
          numeric
        />
        <DataField
          label="Longitude"
          value={formatNumber(longitude, { digits: 4 })}
          numeric
        />
        <DataField
          label="Track"
          value={track === null ? NOT_REPORTED : `${formatNumber(track)}° true`}
          numeric
        />
        <DataField
          label="Altitude"
          value={
            baroAltitude === null
              ? NOT_REPORTED
              : formatNumber(baroAltitude, { unit: 'ft' })
          }
          numeric
        />
        <DataField
          label="Speed"
          value={
            groundSpeed === null
              ? NOT_REPORTED
              : formatNumber(groundSpeed, { unit: 'kt' })
          }
          numeric
        />
        <DataField
          label="Position age"
          value={ageSeconds === null ? NOT_REPORTED : formatAge(ageSeconds)}
          numeric
        />
        <DataField
          label="Position time"
          value={formatUtcDateTime(positionIso)}
          numeric
        />
        {current != null ? (
          <>
            <DataField
              label="Vertical rate"
              value={
                asNumber(current.vertical_rate_fpm) === null
                  ? NOT_REPORTED
                  : formatNumber(current.vertical_rate_fpm, { unit: 'fpm' })
              }
              numeric
            />
            <DataField
              label="On ground"
              value={formatBoolean(current.on_ground)}
            />
            <DataField
              label="Freshness"
              value={
                current.freshness_status ? (
                  <StatusPill
                    size="sm"
                    presentation={presentFreshness(current.freshness_status)}
                  />
                ) : (
                  NOT_REPORTED
                )
              }
            />
            <DataField
              label="Geometric altitude"
              value={
                asNumber(current.geo_altitude_ft) === null
                  ? NOT_REPORTED
                  : formatNumber(current.geo_altitude_ft, { unit: 'ft' })
              }
              numeric
            />
            <DataField
              label="Current H3 cell"
              value={asString(current.current_h3_cell) ?? NOT_REPORTED}
              numeric
            />
            <DataField
              label="Origin country"
              value={asString(current.origin_country) ?? NOT_REPORTED}
            />
          </>
        ) : null}
      </DataFieldGrid>
    </Section>
  );
}

function ProjectionSection({
  projection,
  points,
  now,
}: {
  projection: AircraftProjection | null;
  points: readonly AircraftProjectionPoint[] | null | undefined;
  now: number;
}) {
  const pointList = asRecordList(points);
  const horizons = uniqueHorizons(pointList);
  const age = secondsSince(projection?.generated_at_utc, now);

  return (
    <Section
      title="Short-term motion projection"
      note="Constant-velocity projection from the last eligible state — not a filed route."
    >
      {projection == null ? (
        <p className={styles.empty}>
          No current short-term motion projection was returned. The API omits
          an expired projection rather than presenting it as current.
        </p>
      ) : (
        <>
          <DataFieldGrid columns={3}>
            <DataField
              label="Horizon"
              value={
                asNumber(projection.projection_horizon_min) === null
                  ? NOT_REPORTED
                  : formatNumber(projection.projection_horizon_min, {
                      unit: 'min',
                    })
              }
              numeric
            />
            <DataField
              label="Confidence"
              value={
                projection.confidence ? (
                  <StatusPill
                    size="sm"
                    presentation={presentConfidence(projection.confidence)}
                  />
                ) : (
                  NOT_REPORTED
                )
              }
            />
            <DataField
              label="Status"
              value={humaniseToken(projection.projection_status)}
            />
            <DataField
              label="Created"
              value={formatUtcDateTime(projection.generated_at_utc)}
              numeric
            />
            <DataField
              label="Valid until"
              value={formatUtcDateTime(projection.valid_until_utc)}
              numeric
            />
            <DataField
              label="Projection age"
              value={age === null ? NOT_REPORTED : formatAge(age)}
              numeric
            />
            <DataField
              label="Point count"
              value={formatNumber(projection.point_count ?? pointList.length)}
              numeric
            />
            <DataField
              label="Horizons in points"
              value={
                horizons.length === 0
                  ? NOT_REPORTED
                  : horizons.map((h) => `${h} min`).join(', ')
              }
              numeric
            />
          </DataFieldGrid>

          {pointList.length > 0 ? (
            <details className={styles.expand}>
              <summary>Projected points ({pointList.length})</summary>
              <ol className={styles.pointList}>
                {pointList.map((point, index) => (
                  <li key={asString(point.point_key) ?? String(index)}>
                    <span className="wv-numeric">
                      {asNumber(point.point_sequence_number) ?? index + 1}
                    </span>
                    <span>
                      {asNumber(point.horizon_min) === null
                        ? NOT_REPORTED
                        : `${formatNumber(point.horizon_min)} min`}
                    </span>
                    <span className="wv-numeric">
                      {formatNumber(point.latitude, { digits: 4 })},{' '}
                      {formatNumber(point.longitude, { digits: 4 })}
                    </span>
                    <span className="wv-numeric">
                      {asNumber(point.estimated_altitude_ft) === null
                        ? NOT_REPORTED
                        : formatNumber(point.estimated_altitude_ft, {
                            unit: 'ft',
                          })}
                    </span>
                    <span>{formatUtcDateTime(point.projected_time_utc)}</span>
                  </li>
                ))}
              </ol>
            </details>
          ) : (
            <p className={styles.empty}>
              Projection metadata is present, but no projection points were
              returned.
            </p>
          )}
        </>
      )}
    </Section>
  );
}

function CurrentContextSection({
  contexts,
  viewed,
  matched,
  expectingMatch,
  viewedKey,
  onView,
}: {
  contexts: readonly AircraftOperationalContext[];
  viewed: AircraftOperationalContext | null;
  matched: AircraftOperationalContext | null;
  expectingMatch: boolean;
  viewedKey: string;
  onView: (key: string) => void;
}) {
  const items = contexts;

  return (
    <Section
      title="Current decision context"
      note="Current aircraft-hazard chain joined by stored IDs. History is below."
    >
      {expectingMatch && items.length > 0 && matched == null ? (
        <Notice tone="warning">
          The selected worklist record is not among this aircraft&apos;s
          current contexts. Matching used stored encounter, risk and alert
          IDs only — not the latest timestamp.
        </Notice>
      ) : null}

      {items.length === 0 ? (
        <p className={styles.empty}>
          No current encounter is supported by the current projection and
          hazard version.
        </p>
      ) : (
        <>
          {items.length > 1 ? (
            <div className={styles.contextSwitch}>
              <p className={styles.contextCount}>
                Current contexts ({items.length})
              </p>
              <div className={styles.chips} role="tablist" aria-label="Current contexts">
                {items.map((context, index) => {
                  const key = contextKey(context, index);
                  const hazardId =
                    asString(context.encounter?.hazard_id) ?? NOT_REPORTED;
                  const hazardType = humaniseToken(context.encounter?.hazard_type);
                  const selected = key === viewedKey;

                  return (
                    <button
                      key={key}
                      type="button"
                      role="tab"
                      aria-selected={selected}
                      className={`${styles.chip} ${selected ? styles.chipActive : ''}`}
                      onClick={() => onView(key)}
                    >
                      {hazardType === NOT_REPORTED
                        ? hazardId
                        : `${hazardType} · ${hazardId}`}
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}

          {viewed != null ? (
            <CurrentContextCard
              context={viewed}
              selected={viewed === matched}
            />
          ) : items.length > 0 && expectingMatch ? null : (
            <p className={styles.empty}>
              Select a current context to inspect the decision chain.
            </p>
          )}
        </>
      )}
    </Section>
  );
}

function CurrentContextCard({
  context,
  selected,
}: {
  context: AircraftOperationalContext;
  selected: boolean;
}) {
  const encounter = context.encounter ?? null;
  const risk = context.risk ?? null;
  const recommendation = context.recommendation ?? null;
  const alert = context.alert ?? null;
  const advisory = asString(recommendation?.primary_action_details?.advisory);
  const riskPresentation = risk
    ? presentRiskLevel(risk.risk_level)
    : null;

  return (
    <article
      className={`${styles.card} ${styles.decisionCard} ${selected ? styles.cardSelected : ''}`}
      aria-current={selected ? 'true' : undefined}
    >
      {selected ? (
        <span className={styles.selectedMark}>Selected context</span>
      ) : null}

      {risk ? (
        <div className={styles.riskBlock}>
          <p className={styles.kicker}>Risk</p>
          <div className={styles.riskRow}>
            <span
              className={`${styles.riskLevel} ${styles[`tone_${riskPresentation?.tone ?? 'unknown'}`]}`}
            >
              <span aria-hidden="true">{riskPresentation?.glyph} </span>
              {riskPresentation?.label.toUpperCase()}
            </span>
            <span className={styles.score}>
              Score {formatRiskScore(risk.risk_score)}
            </span>
            {risk.confidence ? (
              <StatusPill
                size="sm"
                prefix="Confidence"
                presentation={presentConfidence(risk.confidence)}
              />
            ) : null}
          </div>
        </div>
      ) : null}

      {recommendation ? (
        <div className={styles.advisoryBlock}>
          <p className={styles.kicker}>Primary advisory</p>
          <p className={styles.advisoryHero}>
            {formatAdvisoryAction(recommendation.primary_action_type)}
          </p>
          {advisory ? <p className={styles.advisory}>{advisory}</p> : null}
          <DataFieldGrid columns={2}>
            {recommendation.confidence ? (
              <DataField
                label="Confidence"
                value={
                  <StatusPill
                    size="sm"
                    presentation={presentConfidence(recommendation.confidence)}
                  />
                }
              />
            ) : null}
            <DataField
              label="Valid until"
              value={formatUtcDateTime(recommendation.valid_until_utc)}
              numeric
            />
            <DataField
              label="Preferred airport"
              value={
                asString(recommendation.preferred_airport_id) ?? NOT_REPORTED
              }
            />
          </DataFieldGrid>
        </div>
      ) : null}

      {alert ? (
        <StatusPill
          size="sm"
          prefix="Alert"
          presentation={presentAlertState(alert.alert_state)}
        />
      ) : null}

      {encounter ? (
        <DataFieldGrid columns={2}>
          <DataField
            label="Hazard ID"
            value={asString(encounter.hazard_id) ?? NOT_REPORTED}
            numeric
          />
          <DataField
            label="Hazard type"
            value={humaniseToken(encounter.hazard_type)}
          />
          <DataField
            label="Inside now"
            value={formatBoolean(encounter.inside_now)}
          />
          <OverlapField
            label="Geometry overlap"
            value={encounter.geometry_overlap_status}
          />
          <OverlapField
            label="Time overlap"
            value={encounter.time_overlap_status}
          />
          <OverlapField
            label="Altitude overlap"
            value={encounter.altitude_overlap_status}
            prefix="Altitude"
          />
          <DataField
            label="Trajectory confidence"
            value={
              encounter.trajectory_confidence ? (
                <StatusPill
                  size="sm"
                  presentation={presentConfidence(
                    encounter.trajectory_confidence,
                  )}
                />
              ) : (
                NOT_REPORTED
              )
            }
          />
          <DataField
            label="Encounter state"
            value={humaniseToken(encounter.encounter_state)}
          />
        </DataFieldGrid>
      ) : (
        <p className={styles.empty}>
          Current context is missing its encounter record.
        </p>
      )}

      <details className={styles.expand}>
        <summary>Record IDs</summary>
        <DataFieldGrid columns={2}>
          <DataField
            label="Encounter ID"
            value={asString(encounter?.encounter_id) ?? NOT_REPORTED}
            numeric
          />
          <DataField
            label="Risk ID"
            value={asString(risk?.risk_id) ?? NOT_REPORTED}
            numeric
          />
          <DataField
            label="Recommendation ID"
            value={asString(recommendation?.recommendation_id) ?? NOT_REPORTED}
            numeric
          />
          <DataField
            label="Alert ID"
            value={asString(alert?.alert_id) ?? NOT_REPORTED}
            numeric
          />
        </DataFieldGrid>
      </details>
    </article>
  );
}

function WhySection({
  context,
}: {
  context: AircraftOperationalContext | null;
}) {
  if (context == null) {
    return null;
  }

  const reasons = [
    ...asStringArray(context.risk?.reasons),
    ...asStringArray(context.recommendation?.reasons),
  ];
  const limitations = [
    ...asStringArray(context.risk?.limitations),
    ...asStringArray(context.recommendation?.limitations),
  ];

  if (reasons.length === 0 && limitations.length === 0) {
    return null;
  }

  return (
    <Section title="Why this is flagged">
      <StringList title="Why" values={dedupeStrings(reasons)} tone="why" />
      <StringList
        title="Uncertainties / limitations"
        values={dedupeStrings(limitations)}
        tone="limit"
      />
    </Section>
  );
}

function EncounterSection({
  encounters,
}: {
  encounters: readonly AircraftHazardEncounter[] | null | undefined;
}) {
  const items = asRecordList(encounters);

  return (
    <Section
      title="Recent encounters"
      note="Historical and superseded rows. Do not combine these with current context as one decision."
    >
      {items.length === 0 ? (
        <p className={styles.empty}>No encounters were returned.</p>
      ) : (
        <div className={styles.stack}>
          {items.map((encounter, index) => (
            <EncounterCard
              key={asString(encounter.encounter_id) ?? String(index)}
              encounter={encounter}
              collapsed={index > 0}
            />
          ))}
        </div>
      )}
    </Section>
  );
}

function EncounterCard({
  encounter,
  collapsed,
}: {
  encounter: AircraftHazardEncounter;
  collapsed: boolean;
}) {
  const body = (
    <DataFieldGrid columns={3}>
      <DataField
        label="Hazard ID"
        value={asString(encounter.hazard_id) ?? NOT_REPORTED}
        numeric
      />
      <DataField
        label="Hazard type"
        value={humaniseToken(encounter.hazard_type)}
      />
      <DataField
        label="Severity"
        value={
          encounter.severity ? (
            <StatusPill
              size="sm"
              presentation={presentHazardSeverity(encounter.severity)}
            />
          ) : (
            NOT_REPORTED
          )
        }
      />
      <DataField
        label="Encounter state"
        value={humaniseToken(encounter.encounter_state)}
      />
      <DataField
        label="Detected"
        value={formatUtcDateTime(encounter.detected_at_utc)}
        numeric
      />
      <DataField
        label="Inside now"
        value={formatBoolean(encounter.inside_now)}
      />
      <OverlapField
        label="Geometry overlap"
        value={encounter.geometry_overlap_status}
      />
      <OverlapField
        label="Time overlap"
        value={encounter.time_overlap_status}
      />
      <OverlapField
        label="Altitude overlap"
        value={encounter.altitude_overlap_status}
        prefix="Altitude"
      />
      <DataField
        label="Corridor intersects"
        value={formatBoolean(encounter.corridor_intersects)}
      />
      <DataField
        label="Centerline intersects"
        value={formatBoolean(encounter.centerline_intersects)}
      />
      <DataField
        label="Exact intersection"
        value={formatBoolean(encounter.exact_intersection_confirmed)}
      />
      <DataField
        label="Trajectory confidence"
        value={
          encounter.trajectory_confidence ? (
            <StatusPill
              size="sm"
              presentation={presentConfidence(encounter.trajectory_confidence)}
            />
          ) : (
            NOT_REPORTED
          )
        }
      />
      <DataField
        label="Valid from"
        value={formatUtcDateTime(encounter.valid_from_utc)}
        numeric
      />
      <DataField
        label="Valid to"
        value={formatUtcDateTime(encounter.valid_to_utc)}
        numeric
      />
      <DataField
        label="Projection"
        value={asString(encounter.projection_id) ?? NOT_REPORTED}
        numeric
      />
    </DataFieldGrid>
  );

  if (!collapsed) {
    return <article className={styles.card}>{body}</article>;
  }

  return (
    <details className={styles.expand}>
      <summary>
        {humaniseToken(encounter.hazard_type)} ·{' '}
        {asString(encounter.hazard_id) ?? NOT_REPORTED}
      </summary>
      {body}
    </details>
  );
}

function RiskSection({
  risks,
}: {
  risks: readonly RiskResult[] | null | undefined;
}) {
  const items = asRecordList(risks);
  const risk = latestRisk(items);

  return (
    <Section
      title="Recent risk results"
      note="History only. Current risk is in Current decision context."
    >
      {risk == null ? (
        <p className={styles.empty}>No risk result was returned.</p>
      ) : (
        <div className={styles.stack}>
          <RiskCard risk={risk} />
          {items.length > 1 ? (
            <details className={styles.expand}>
              <summary>Earlier risk results ({items.length - 1})</summary>
              <div className={styles.stack}>
                {items.slice(1).map((item, index) => (
                  <RiskCard
                    key={asString(item.risk_id) ?? String(index)}
                    risk={item}
                  />
                ))}
              </div>
            </details>
          ) : null}
        </div>
      )}
    </Section>
  );
}

function RiskCard({ risk }: { risk: RiskResult }) {
  return (
    <article className={styles.card}>
      <div className={styles.cardHeader}>
        <StatusPill
          size="sm"
          prefix="Level"
          presentation={presentRiskLevel(risk.risk_level)}
        />
        <span className={styles.score}>
          Score {formatRiskScore(risk.risk_score)}
        </span>
        {risk.confidence ? (
          <StatusPill
            size="sm"
            prefix="Confidence"
            presentation={presentConfidence(risk.confidence)}
          />
        ) : null}
        {risk.freshness_status ? (
          <StatusPill
            size="sm"
            prefix="Freshness"
            presentation={presentFreshness(risk.freshness_status)}
          />
        ) : null}
      </div>

      <DataFieldGrid columns={3}>
        <DataField
          label="Generated"
          value={formatUtcDateTime(risk.generated_at_utc)}
          numeric
        />
        <DataField
          label="Valid until"
          value={formatUtcDateTime(risk.valid_until_utc)}
          numeric
        />
        <DataField
          label="Encounter ID"
          value={asString(risk.encounter_id) ?? NOT_REPORTED}
          numeric
        />
        <DataField
          label="Hazard component"
          value={formatNumber(risk.hazard_component_score)}
          numeric
        />
        <DataField
          label="Geometry component"
          value={formatNumber(risk.geometry_component_score)}
          numeric
        />
        <DataField
          label="Time component"
          value={formatNumber(risk.time_component_score)}
          numeric
        />
        <DataField
          label="Altitude component"
          value={formatNumber(risk.altitude_component_score)}
          numeric
        />
        <DataField
          label="Confidence component"
          value={formatNumber(risk.confidence_component_score)}
          numeric
        />
        <DataField
          label="Freshness component"
          value={formatNumber(risk.freshness_component_score)}
          numeric
        />
        <DataField
          label="Data quality component"
          value={formatNumber(risk.data_quality_component_score)}
          numeric
        />
      </DataFieldGrid>

      <StringList title="Reasons" values={asStringArray(risk.reasons)} />
      <StringList
        title="Data limitations"
        values={asStringArray(risk.limitations)}
      />
    </article>
  );
}

function RecommendationSection({
  recommendations,
}: {
  recommendations: readonly Recommendation[] | null | undefined;
}) {
  const items = asRecordList(recommendations);
  const recommendation = latestRecommendation(items);

  return (
    <Section
      title="Recent recommendations"
      note="Historical advisory rows. Current recommendation is joined in Current decision context."
    >
      {recommendation == null ? (
        <p className={styles.empty}>No recommendation was returned.</p>
      ) : (
        <div className={styles.stack}>
          <RecommendationCard recommendation={recommendation} />
          {items.length > 1 ? (
            <details className={styles.expand}>
              <summary>
                Earlier recommendations ({items.length - 1})
              </summary>
              <div className={styles.stack}>
                {items.slice(1).map((item, index) => (
                  <RecommendationCard
                    key={asString(item.recommendation_id) ?? String(index)}
                    recommendation={item}
                  />
                ))}
              </div>
            </details>
          ) : null}
        </div>
      )}
    </Section>
  );
}

function RecommendationCard({
  recommendation,
}: {
  recommendation: Recommendation;
}) {
  const advisory = asString(recommendation.primary_action_details?.advisory);
  const alternatives = asRecordList(recommendation.alternative_actions);
  const candidates = asRecordList(recommendation.candidate_airport_summaries);
  const evidence = asRecordList(recommendation.evidence_references)
    .map(formatEvidenceReference)
    .filter((line): line is string => line !== null);
  const sourceVersions = formatSourceVersions(recommendation.source_versions);

  return (
    <article className={styles.card}>
      <div className={styles.cardHeader}>
        <span className={styles.action}>
          {formatAdvisoryAction(recommendation.primary_action_type)}
        </span>
        {recommendation.confidence ? (
          <StatusPill
            size="sm"
            prefix="Confidence"
            presentation={presentConfidence(recommendation.confidence)}
          />
        ) : null}
      </div>

      {advisory ? <p className={styles.advisory}>{advisory}</p> : null}

      {asString(recommendation.advisory_notice) ? (
        <p className={styles.advisoryNotice}>
          {asString(recommendation.advisory_notice)}
        </p>
      ) : null}

      <DataFieldGrid columns={3}>
        <DataField
          label="Valid from"
          value={formatUtcDateTime(recommendation.valid_from_utc)}
          numeric
        />
        <DataField
          label="Valid until"
          value={formatUtcDateTime(recommendation.valid_until_utc)}
          numeric
        />
        <DataField
          label="Preferred airport"
          value={asString(recommendation.preferred_airport_id) ?? NOT_REPORTED}
        />
        <DataField
          label="Preferred score"
          value={formatNumber(recommendation.preferred_airport_score)}
          numeric
        />
        <DataField
          label="No suitable candidate"
          value={
            asString(recommendation.no_suitable_candidate_reason) ??
            NOT_REPORTED
          }
        />
        <DataField
          label="Ruleset"
          value={asString(recommendation.ruleset_version) ?? NOT_REPORTED}
          numeric
        />
      </DataFieldGrid>

      <StringList title="Reasons" values={asStringArray(recommendation.reasons)} />
      <StringList
        title="Limitations"
        values={asStringArray(recommendation.limitations)}
      />

      {alternatives.length > 0 ? (
        <div className={styles.listBlock}>
          <h4 className={styles.listHeading}>Alternatives</h4>
          <ul className={styles.reasonList}>
            {alternatives.map((alternative, index) => (
              <li key={asString(alternative.airport_id) ?? String(index)}>
                {formatAdvisoryAction(alternative.type)}
                {asString(alternative.airport_id)
                  ? ` · ${asString(alternative.airport_id)}`
                  : ''}
                {asNumber(alternative.score) === null
                  ? ''
                  : ` · score ${formatNumber(alternative.score)}`}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {candidates.length > 0 ? (
        <div className={styles.listBlock}>
          <h4 className={styles.listHeading}>Candidate airports</h4>
          <ul className={styles.reasonList}>
            {candidates.map((candidate, index) => (
              <li key={asString(candidate.airport_id) ?? String(index)}>
                {asString(candidate.airport_id) ?? NOT_REPORTED}
                {asNumber(candidate.rank) === null
                  ? ''
                  : ` · rank ${formatNumber(candidate.rank)}`}
                {asNumber(candidate.total_airport_score) === null
                  ? ''
                  : ` · score ${formatNumber(candidate.total_airport_score)}`}
                {asNumber(candidate.distance_nm) === null
                  ? ''
                  : ` · ${formatNumber(candidate.distance_nm, { unit: 'nm' })}`}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <StringList title="Evidence" values={evidence} />
      <StringList title="Source versions" values={sourceVersions} />
    </article>
  );
}

function AlertSection({
  alerts,
}: {
  alerts: readonly ActiveAlert[] | null | undefined;
}) {
  const items = asRecordList(alerts);

  return (
    <Section
      title="Recent alerts"
      note="Historical alert rows. Current alert is joined in Current decision context."
    >
      {items.length === 0 ? (
        <p className={styles.empty}>No alerts were returned.</p>
      ) : (
        <div className={styles.stack}>
          {items.map((alert, index) => (
            <AlertCard
              key={asString(alert.fingerprint) ?? asString(alert.alert_id) ?? String(index)}
              alert={alert}
            />
          ))}
        </div>
      )}
    </Section>
  );
}

function AlertCard({ alert }: { alert: ActiveAlert }) {
  return (
    <article className={styles.card}>
      <div className={styles.cardHeader}>
        <StatusPill
          size="sm"
          prefix="State"
          presentation={presentAlertState(alert.alert_state)}
        />
        {alert.risk_level ? (
          <StatusPill
            size="sm"
            prefix="Risk"
            presentation={presentRiskLevel(alert.risk_level)}
          />
        ) : null}
      </div>

      {asString(alert.message) ? (
        <p className={styles.advisory}>{asString(alert.message)}</p>
      ) : null}

      <DataFieldGrid columns={3}>
        <DataField
          label="Updated"
          value={formatUtcDateTime(alert.updated_at_utc)}
          numeric
        />
        <DataField
          label="Created"
          value={formatUtcDateTime(alert.created_at_utc)}
          numeric
        />
        <DataField
          label="Valid until"
          value={formatUtcDateTime(alert.valid_until_utc)}
          numeric
        />
        <DataField
          label="State reason"
          value={humaniseToken(alert.state_reason)}
        />
        <DataField
          label="Risk score"
          value={formatRiskScore(alert.risk_score)}
          numeric
        />
        <DataField
          label="Alert ID"
          value={asString(alert.alert_id) ?? NOT_REPORTED}
          numeric
        />
      </DataFieldGrid>
    </article>
  );
}

function Section({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: ReactNode;
}) {
  return (
    <section className={styles.section}>
      <header className={styles.sectionHeader}>
        <h3 className={styles.sectionTitle}>{title}</h3>
        {note ? <p className={styles.sectionNote}>{note}</p> : null}
      </header>
      {children}
    </section>
  );
}

function OverlapField({
  label,
  value,
  prefix,
}: {
  label: string;
  value: unknown;
  prefix?: string;
}) {
  const raw = asString(value);

  return (
    <DataField
      label={label}
      value={
        raw === null ? (
          NOT_REPORTED
        ) : (
          <StatusPill
            size="sm"
            prefix={prefix}
            presentation={presentOverlapStatus(raw)}
          />
        )
      }
    />
  );
}

function StringList({
  title,
  values,
  tone,
}: {
  title: string;
  values: string[];
  tone?: 'why' | 'limit';
}) {
  if (values.length === 0) {
    return null;
  }

  return (
    <div className={styles.listBlock}>
      <h4 className={styles.listHeading}>{title}</h4>
      <ul
        className={
          tone === 'why'
            ? styles.whyList
            : tone === 'limit'
              ? styles.limitList
              : styles.reasonList
        }
      >
        {values.map((value, index) => (
          <li key={`${title}:${index}:${value}`}>
            {tone === 'why' ? (
              <span className={styles.listMark} aria-hidden="true">
                ✓
              </span>
            ) : null}
            {tone === 'limit' ? (
              <span className={styles.listMark} aria-hidden="true">
                !
              </span>
            ) : null}
            <span>{value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function dedupeStrings(values: readonly string[]): string[] {
  const seen = new Set<string>();
  const unique: string[] = [];

  for (const value of values) {
    if (seen.has(value)) {
      continue;
    }

    seen.add(value);
    unique.push(value);
  }

  return unique;
}
