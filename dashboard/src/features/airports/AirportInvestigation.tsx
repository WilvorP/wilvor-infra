import { Link } from 'react-router-dom';
import type { ReactNode } from 'react';

import { describeApiError, isApiError } from '@/api/errors';
import { DataField, DataFieldGrid } from '@/components/DataField';
import { Notice } from '@/components/QueryState';
import { StatusPill } from '@/components/StatusPill';
import { OperationsMap } from '@/features/map/OperationsMap';
import { buildAirportGeoJson } from '@/features/map/airportGeoJson';
import { useAirportDetail } from '@/hooks/useOperationalQueries';
import type {
  AirportAssessment,
  AirportStatus,
  MetarLatest,
  TafForecastPeriod,
  TafLatest,
} from '@/types/api';
import { asNumber, asString, asStringArray } from '@/utils/coerce';
import {
  formatAge,
  formatBoolean,
  formatNumber,
  formatUtcDateTime,
  humaniseToken,
  NOT_REPORTED,
  secondsSince,
} from '@/utils/format';
import {
  presentAssessmentStatus,
  presentFlightCategory,
  presentFreshness,
  presentRiskLevel,
  presentWeatherImpact,
} from '@/utils/status';

import styles from './AirportInvestigation.module.css';

export interface AirportInvestigationProps {
  airportId: string;
  mapStyleUrl: string | null;
  now?: number;
}

export function AirportInvestigation({
  airportId,
  mapStyleUrl,
  now = Date.now(),
}: AirportInvestigationProps) {
  const detailQuery = useAirportDetail(airportId);
  const detail = detailQuery.data;
  const pending = detailQuery.isPending && detail === undefined;
  const failed = detailQuery.isError && detail === undefined;
  const stale = detailQuery.isError && detail !== undefined;
  const notFound =
    detailQuery.isError &&
    isApiError(detailQuery.error) &&
    detailQuery.error.kind === 'client' &&
    detailQuery.error.status === 404;

  const airport = detail?.airport ?? null;
  const geoJson = buildAirportGeoJson(airport ? [airport] : []);

  return (
    <div className={styles.page}>
      <header className={styles.chrome}>
        <div className={styles.chromeRow}>
          <Link className={styles.back} to="/airports">
            Airports
          </Link>
          <div className={styles.identity}>
            <span className={styles.icao}>{airportId}</span>
            {asString(airport?.station_name) ? (
              <span className={styles.name}>{asString(airport?.station_name)}</span>
            ) : null}
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
              <StatusPill
                size="sm"
                prefix="METAR"
                presentation={presentFreshness(airport.metar_freshness_status)}
              />
            </>
          ) : null}
        </div>
      </header>

      {pending ? <Notice>Loading airport investigation…</Notice> : null}

      {stale ? (
        <Notice tone="warning">
          Showing the last successful airport refresh. The most recent update
          failed: {describeApiError(detailQuery.error)}
        </Notice>
      ) : null}

      {failed && notFound ? (
        <Notice tone="warning">
          No current AirportStatus record exists for {airportId}. Missing is
          not treated as Normal.
        </Notice>
      ) : null}

      {failed && !notFound ? (
        <Notice tone="warning">
          Airport investigation is unavailable. {describeApiError(detailQuery.error)}
        </Notice>
      ) : null}

      <div className={styles.layout}>
        <div className={styles.mapSlot}>
          <OperationsMap
            styleUrl={mapStyleUrl}
            selectedHazardId={null}
            onSelectHazard={() => {}}
            selectedAircraftId={null}
            onSelectAircraft={() => {}}
            showAircraft={false}
            airports={geoJson.collection}
            selectedAirportId={airportId}
          />
        </div>

        {airport ? (
          <CurrentStatusSection airport={airport} now={now} />
        ) : !pending && !failed ? (
          <Section title="Current operational status">
            <p className={styles.empty}>
              No current airport data available.
            </p>
          </Section>
        ) : (
          <Section title="Current operational status">
            <p className={styles.empty}>Waiting for the stored airport record.</p>
          </Section>
        )}

        <MetarSection
          airport={airport}
          metar={detail?.metar ?? null}
          now={now}
          ready={detail !== undefined}
        />

        <TafSection
          airport={airport}
          taf={detail?.taf ?? null}
          periods={detail?.tafForecastPeriods}
          now={now}
          ready={detail !== undefined}
        />

        {detail !== undefined ? (
          <AssessmentsSection assessments={detail.recentAssessments} />
        ) : null}
      </div>
    </div>
  );
}

function CurrentStatusSection({
  airport,
  now,
}: {
  airport: AirportStatus;
  now: number;
}) {
  const age = secondsSince(airport.updated_at_utc, now);

  return (
    <Section
      title="Current operational status"
      note="Stored AirportStatus. Weather impact and risk are backend classifications."
    >
      <div className={styles.statusRow}>
        <StatusPill
          presentation={presentWeatherImpact(airport.weather_impact_status)}
        />
        <StatusPill
          prefix="Wx risk"
          presentation={presentRiskLevel(airport.weather_risk_level)}
        />
        <StatusPill
          presentation={presentAssessmentStatus(airport.assessment_status)}
        />
      </div>

      <DataFieldGrid columns={2}>
        <DataField
          label="Diversion weather ready"
          value={formatBoolean(airport.is_diversion_weather_ready)}
        />
        <DataField
          label="Status updated"
          value={
            age === null
              ? formatUtcDateTime(airport.updated_at_utc)
              : `${formatUtcDateTime(airport.updated_at_utc)} · ${formatAge(age)}`
          }
          numeric
        />
        <DataField
          label="Position"
          value={
            asNumber(airport.latitude) === null ||
            asNumber(airport.longitude) === null
              ? NOT_REPORTED
              : `${formatNumber(airport.latitude, { digits: 4 })}, ${formatNumber(airport.longitude, { digits: 4 })}`
          }
          numeric
        />
        <DataField
          label="IATA"
          value={asString(airport.iata_code) ?? NOT_REPORTED}
        />
      </DataFieldGrid>

      <StringList title="Why" values={asStringArray(airport.status_reasons)} tone="why" />
      <StringList
        title="Uncertainties / limitations"
        values={asStringArray(airport.known_limitations)}
        tone="limit"
      />
    </Section>
  );
}

function MetarSection({
  airport,
  metar,
  now,
  ready,
}: {
  airport: AirportStatus | null;
  metar: MetarLatest | null;
  now: number;
  ready: boolean;
}) {
  if (!ready) {
    return (
      <Section title="Current observation / METAR">
        <p className={styles.empty}>Waiting for airport detail.</p>
      </Section>
    );
  }

  if (metar == null) {
    return (
      <Section
        title="Current observation / METAR"
        note="Observed weather. Distinct from TAF forecast and airport assessment."
      >
        <p className={styles.empty}>Current METAR unavailable.</p>
        {airport?.metar_freshness_status ? (
          <StatusPill
            size="sm"
            prefix="METAR"
            presentation={presentFreshness(airport.metar_freshness_status)}
          />
        ) : null}
      </Section>
    );
  }

  const age =
    metar.observed_time_epoch != null
      ? Math.max(0, Math.round(now / 1000 - Number(metar.observed_time_epoch)))
      : secondsSince(metar.observed_time_utc, now);

  return (
    <Section
      title="Current observation / METAR"
      note="Observed weather. Distinct from TAF forecast and airport assessment."
    >
      <div className={styles.statusRow}>
        <StatusPill
          prefix="Cat"
          presentation={presentFlightCategory(metar.flight_category)}
        />
        <StatusPill
          prefix="Freshness"
          presentation={presentFreshness(metar.freshness_status)}
        />
      </div>

      <DataFieldGrid columns={3}>
        <DataField
          label="Observed"
          value={formatUtcDateTime(metar.observed_time_utc)}
          numeric
        />
        <DataField
          label="Age"
          value={age === null ? NOT_REPORTED : formatAge(age)}
          numeric
        />
        <DataField
          label="Visibility"
          value={
            asNumber(metar.visibility_sm) === null
              ? NOT_REPORTED
              : formatNumber(metar.visibility_sm, { digits: 1, unit: 'SM' })
          }
          numeric
        />
        <DataField
          label="Ceiling"
          value={
            asNumber(metar.ceiling_ft) === null
              ? NOT_REPORTED
              : formatNumber(metar.ceiling_ft, { unit: 'ft' })
          }
          numeric
        />
        <DataField
          label="Wind"
          value={formatWind(
            metar.wind_direction_deg,
            metar.wind_speed_kt,
            metar.wind_gust_kt,
          )}
          numeric
        />
        <DataField
          label="Temperature / dew point"
          value={formatTempDew(metar.temperature_c, metar.dewpoint_c)}
          numeric
        />
        <DataField
          label="Weather"
          value={
            asString(metar.weather_string) ??
            (asStringArray(metar.weather_codes).join(' ') || NOT_REPORTED)
          }
        />
      </DataFieldGrid>

      {asString(metar.raw_text) ? (
        <details className={styles.expand}>
          <summary>Raw METAR</summary>
          <p className={`${styles.raw} wv-numeric`}>{asString(metar.raw_text)}</p>
        </details>
      ) : null}
    </Section>
  );
}

function TafSection({
  airport,
  taf,
  periods,
  now,
  ready,
}: {
  airport: AirportStatus | null;
  taf: TafLatest | null;
  periods: readonly TafForecastPeriod[] | null | undefined;
  now: number;
  ready: boolean;
}) {
  if (!ready) {
    return (
      <Section title="Forecast / TAF">
        <p className={styles.empty}>Waiting for airport detail.</p>
      </Section>
    );
  }

  if (taf == null) {
    return (
      <Section
        title="Forecast / TAF"
        note="Forecast information. Not a current observation."
      >
        <p className={styles.empty}>TAF unavailable.</p>
        {airport?.taf_freshness_status ? (
          <StatusPill
            size="sm"
            prefix="TAF"
            presentation={presentFreshness(airport.taf_freshness_status)}
          />
        ) : null}
      </Section>
    );
  }

  const ordered = orderedTafPeriods(periods);
  const age = secondsSince(taf.issued_at_utc, now);

  return (
    <Section
      title="Forecast / TAF"
      note="Forecast periods are shown in stored chronological order. BASE, TEMPO and PROB stay separate."
    >
      <div className={styles.statusRow}>
        <StatusPill
          prefix="Freshness"
          presentation={presentFreshness(taf.freshness_status)}
        />
        {taf.is_amendment === true ? (
          <span className={styles.flag}>Amendment</span>
        ) : null}
        {taf.is_correction === true ? (
          <span className={styles.flag}>Correction</span>
        ) : null}
      </div>

      <DataFieldGrid columns={3}>
        <DataField
          label="Issued"
          value={
            age === null
              ? formatUtcDateTime(taf.issued_at_utc)
              : `${formatUtcDateTime(taf.issued_at_utc)} · ${formatAge(age)}`
          }
          numeric
        />
        <DataField
          label="Valid from"
          value={formatUtcDateTime(taf.valid_from_utc)}
          numeric
        />
        <DataField
          label="Valid to"
          value={formatUtcDateTime(taf.valid_to_utc)}
          numeric
        />
        <DataField
          label="Period count"
          value={formatNumber(taf.forecast_period_count ?? ordered.length)}
          numeric
        />
        <DataField
          label="Period materialization"
          value={humaniseToken(taf.period_materialization_status)}
        />
      </DataFieldGrid>

      {ordered.length === 0 ? (
        <p className={styles.empty}>
          TAF metadata is present, but no forecast periods were returned in the
          operational window.
        </p>
      ) : (
        <ol className={styles.periods}>
          {ordered.map((period, index) => (
            <li key={asString(period.period_id) ?? asString(period.period_key) ?? String(index)}>
              <div className={styles.periodHead}>
                <span className={styles.change}>
                  {asString(period.change_type) ?? NOT_REPORTED}
                </span>
                <span className="wv-numeric">
                  {formatUtcDateTime(period.period_from_utc)} →{' '}
                  {formatUtcDateTime(period.period_to_utc)}
                </span>
                <StatusPill
                  size="sm"
                  prefix="Cat"
                  presentation={presentFlightCategory(
                    period.forecast_flight_category,
                  )}
                />
              </div>
              <DataFieldGrid columns={3}>
                <DataField
                  label="Probability"
                  value={
                    asNumber(period.probability) === null
                      ? NOT_REPORTED
                      : formatNumber(period.probability, { unit: '%' })
                  }
                  numeric
                />
                <DataField
                  label="Visibility"
                  value={
                    asNumber(period.visibility_sm) === null
                      ? NOT_REPORTED
                      : formatNumber(period.visibility_sm, {
                          digits: 1,
                          unit: 'SM',
                        })
                  }
                  numeric
                />
                <DataField
                  label="Ceiling"
                  value={
                    asNumber(period.ceiling_ft) === null
                      ? NOT_REPORTED
                      : formatNumber(period.ceiling_ft, { unit: 'ft' })
                  }
                  numeric
                />
                <DataField
                  label="Wind"
                  value={formatWind(
                    period.wind_direction_deg,
                    period.wind_speed_kt,
                    period.wind_gust_kt,
                  )}
                  numeric
                />
                <DataField
                  label="Weather"
                  value={asString(period.weather_string) ?? NOT_REPORTED}
                />
              </DataFieldGrid>
            </li>
          ))}
        </ol>
      )}

      {asString(taf.raw_text) ? (
        <details className={styles.expand}>
          <summary>Raw TAF</summary>
          <p className={`${styles.raw} wv-numeric`}>{asString(taf.raw_text)}</p>
        </details>
      ) : null}
    </Section>
  );
}

function AssessmentsSection({
  assessments,
}: {
  assessments: readonly AirportAssessment[] | null | undefined;
}) {
  const items = [...(assessments ?? [])];

  return (
    <details className={styles.history}>
      <summary>Recent diversion assessments ({items.length})</summary>
      <p className={styles.sectionNote}>
        These are recent aircraft/risk candidate evaluations for this airport,
        not a standalone current airport score. Congestion evidence is stored
        as UNAVAILABLE.
      </p>
      {items.length === 0 ? (
        <p className={styles.empty}>
          Airport assessment has not been generated.
        </p>
      ) : (
        <div className={styles.stack}>
          {items.map((item, index) => (
            <article
              key={asString(item.airport_assessment_id) ?? String(index)}
              className={styles.card}
            >
              <div className={styles.statusRow}>
                <StatusPill
                  size="sm"
                  presentation={presentAssessmentStatus(item.assessment_status)}
                />
                <StatusPill
                  size="sm"
                  prefix="Wx risk"
                  presentation={presentRiskLevel(item.weather_risk_level)}
                />
              </div>
              <DataFieldGrid columns={3}>
                <DataField
                  label="Aircraft"
                  value={asString(item.aircraft_id) ?? NOT_REPORTED}
                  numeric
                />
                <DataField
                  label="Distance"
                  value={
                    asNumber(item.distance_nm) === null
                      ? NOT_REPORTED
                      : formatNumber(item.distance_nm, { digits: 1, unit: 'nm' })
                  }
                  numeric
                />
                <DataField
                  label="ETA"
                  value={
                    asNumber(item.eta_minutes) === null
                      ? NOT_REPORTED
                      : formatNumber(item.eta_minutes, { unit: 'min' })
                  }
                  numeric
                />
                <DataField
                  label="Weather score"
                  value={formatNumber(item.weather_score)}
                  numeric
                />
                <DataField
                  label="TAF score"
                  value={formatNumber(item.taf_score)}
                  numeric
                />
                <DataField
                  label="Total score"
                  value={formatNumber(item.total_airport_score)}
                  numeric
                />
                <DataField
                  label="Congestion evidence"
                  value={humaniseToken(item.congestion_evidence_status)}
                />
                <DataField
                  label="Runway evidence"
                  value={humaniseToken(item.runway_evidence_status)}
                />
                <DataField
                  label="Created"
                  value={formatUtcDateTime(item.created_at_utc)}
                  numeric
                />
              </DataFieldGrid>
              <StringList
                title="Limitations"
                values={asStringArray(item.known_limitations)}
                tone="limit"
              />
            </article>
          ))}
        </div>
      )}
    </details>
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
      <ul className={tone === 'limit' ? styles.limitList : styles.whyList}>
        {values.map((value, index) => (
          <li key={`${title}:${index}:${value}`}>
            <span aria-hidden="true">{tone === 'limit' ? '!' : '✓'}</span>
            <span>{value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function orderedTafPeriods(
  periods: readonly TafForecastPeriod[] | null | undefined,
): TafForecastPeriod[] {
  return [...(periods ?? [])].sort((left, right) => {
    const from =
      (asNumber(left.period_from_epoch) ?? 0) -
      (asNumber(right.period_from_epoch) ?? 0);

    if (from !== 0) {
      return from;
    }

    return (
      (asNumber(left.sequence_number) ?? 0) -
      (asNumber(right.sequence_number) ?? 0)
    );
  });
}

function formatWind(
  direction: unknown,
  speed: unknown,
  gust: unknown,
): string {
  if (asNumber(direction) === null && asNumber(speed) === null) {
    return NOT_REPORTED;
  }

  const dir =
    asNumber(direction) === null ? NOT_REPORTED : `${formatNumber(direction)}°`;
  const spd =
    asNumber(speed) === null
      ? NOT_REPORTED
      : formatNumber(speed, { unit: 'kt' });
  const gustPart =
    asNumber(gust) === null ? '' : ` G${formatNumber(gust, { unit: 'kt' })}`;

  return `${dir} / ${spd}${gustPart}`;
}

function formatTempDew(temp: unknown, dew: unknown): string {
  if (asNumber(temp) === null && asNumber(dew) === null) {
    return NOT_REPORTED;
  }

  const t =
    asNumber(temp) === null ? NOT_REPORTED : formatNumber(temp, { digits: 1, unit: '°C' });
  const d =
    asNumber(dew) === null ? NOT_REPORTED : formatNumber(dew, { digits: 1, unit: '°C' });

  return `${t} / ${d}`;
}
