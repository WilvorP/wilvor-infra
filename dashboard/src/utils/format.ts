import { asBoolean, asNumber, asString } from './coerce';

/**
 * Presentation formatting.
 *
 * Nothing here derives operational meaning. Precision is deliberately capped
 * at what the upstream sources actually support: OpenSky state vectors are
 * periodic samples, so rendering more significant figures than the source
 * provides would overstate the platform's accuracy.
 */

/** Rendered wherever a value is absent, so blanks are never ambiguous. */
export const NOT_REPORTED = '—';

export function formatCount(value: unknown): string {
  const count = asNumber(value);

  return count === null ? NOT_REPORTED : count.toLocaleString('en-US');
}

export function formatNumber(
  value: unknown,
  options: { digits?: number; unit?: string } = {},
): string {
  const numeric = asNumber(value);

  if (numeric === null) {
    return NOT_REPORTED;
  }

  const digits = options.digits ?? 0;
  const formatted = numeric.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });

  return options.unit ? `${formatted} ${options.unit}` : formatted;
}

/** Risk scores are 0-100 integers produced by the risk processor. */
export function formatRiskScore(value: unknown): string {
  const score = asNumber(value);

  return score === null ? NOT_REPORTED : String(Math.round(score));
}

/**
 * Compact relative age, e.g. "12s", "4m 20s", "3h 05m".
 *
 * Ages are reported rather than absolute times because the operator question
 * is "how old is this?", not "when exactly was it sampled?".
 */
export function formatAge(seconds: unknown): string {
  const total = asNumber(seconds);

  if (total === null || total < 0) {
    return NOT_REPORTED;
  }

  const whole = Math.floor(total);

  if (whole < 60) {
    return `${whole}s`;
  }

  if (whole < 3600) {
    const minutes = Math.floor(whole / 60);
    const remainder = whole % 60;
    return `${minutes}m ${String(remainder).padStart(2, '0')}s`;
  }

  if (whole < 86400) {
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor((whole % 3600) / 60);
    return `${hours}h ${String(minutes).padStart(2, '0')}m`;
  }

  const days = Math.floor(whole / 86400);
  const hours = Math.floor((whole % 86400) / 3600);
  return `${days}d ${String(hours).padStart(2, '0')}h`;
}

function parseTimestamp(value: unknown): Date | null {
  const text = asString(value);

  if (text === null) {
    return null;
  }

  const parsed = new Date(text);

  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/**
 * Absolute UTC clock time, e.g. "14:32:07Z".
 *
 * Operations run on UTC. Rendering browser-local time would invite
 * misreading against METAR/TAF/SIGMET validity windows, which are all UTC.
 */
export function formatUtcTime(value: unknown): string {
  const date = parseTimestamp(value);

  if (date === null) {
    return NOT_REPORTED;
  }

  const hh = String(date.getUTCHours()).padStart(2, '0');
  const mm = String(date.getUTCMinutes()).padStart(2, '0');
  const ss = String(date.getUTCSeconds()).padStart(2, '0');

  return `${hh}:${mm}:${ss}Z`;
}

/** Date and UTC clock time, e.g. "2026-09-03 14:32:07Z". */
export function formatUtcDateTime(value: unknown): string {
  const date = parseTimestamp(value);

  if (date === null) {
    return NOT_REPORTED;
  }

  return `${date.toISOString().slice(0, 10)} ${formatUtcTime(value)}`;
}

/** Seconds elapsed since an ISO timestamp, or `null` if unparseable. */
export function secondsSince(value: unknown, now: number = Date.now()):
  | number
  | null {
  const date = parseTimestamp(value);

  if (date === null) {
    return null;
  }

  return Math.max(0, Math.round((now - date.getTime()) / 1000));
}

/** Boolean attribute as Yes / No, or not-reported when the field is absent. */
export function formatBoolean(value: unknown): string {
  const flag = asBoolean(value);

  if (flag === null) {
    return NOT_REPORTED;
  }

  return flag ? 'Yes' : 'No';
}

/** Uppercase enum token to display text, e.g. "WEATHER_IMPACTED" -> "Weather impacted". */
export function humaniseToken(value: unknown): string {
  const text = asString(value);

  if (text === null) {
    return NOT_REPORTED;
  }

  const spaced = text.replace(/_/g, ' ').toLowerCase();

  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** Aircraft identity for display: callsign when known, else the ICAO24 id. */
export function formatAircraftLabel(
  callsign: unknown,
  aircraftId: unknown,
): string {
  return (
    asString(callsign) ?? asString(aircraftId)?.toUpperCase() ?? NOT_REPORTED
  );
}
