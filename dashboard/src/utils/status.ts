import { asBoolean, asString } from './coerce';
import { humaniseToken } from './format';

/**
 * Status vocabularies shared by the design-system primitives.
 *
 * Accessibility rule enforced here: every tone is paired with a short glyph
 * and a text label, so status is never conveyed by colour alone.
 */

export type StatusTone =
  | 'high'
  | 'medium'
  | 'low'
  | 'neutral'
  | 'positive'
  | 'unknown';

export interface StatusPresentation {
  readonly tone: StatusTone;
  readonly label: string;
  /** Short non-colour marker rendered alongside the label. */
  readonly glyph: string;
}

const UNKNOWN_PRESENTATION: StatusPresentation = {
  tone: 'unknown',
  label: 'Unknown',
  glyph: '?',
};

/** Risk levels emitted by the risk processor. */
export function presentRiskLevel(value: unknown): StatusPresentation {
  switch (asString(value)?.toUpperCase()) {
    case 'HIGH':
      return { tone: 'high', label: 'High', glyph: '▲' };
    case 'MEDIUM':
      return { tone: 'medium', label: 'Medium', glyph: '◆' };
    case 'LOW':
      return { tone: 'low', label: 'Low', glyph: '▬' };
    case 'UNKNOWN':
      return UNKNOWN_PRESENTATION;
    default:
      return UNKNOWN_PRESENTATION;
  }
}

/**
 * Freshness banding.
 *
 * Handles both backend vocabularies: the age-banded one used for aircraft,
 * METAR and TAF, and the availability-only one used for SIGMET, where an
 * unchanged valid product must not be reported as stale.
 */
export function presentFreshness(value: unknown): StatusPresentation {
  switch (asString(value)?.toUpperCase()) {
    case 'FRESH':
      return { tone: 'positive', label: 'Fresh', glyph: '●' };
    case 'ACCEPTABLE':
      return { tone: 'medium', label: 'Acceptable', glyph: '◐' };
    case 'STALE':
      return { tone: 'high', label: 'Stale', glyph: '○' };
    case 'AVAILABLE':
      return { tone: 'positive', label: 'Available', glyph: '●' };
    case 'UNAVAILABLE':
      return { tone: 'unknown', label: 'Unavailable', glyph: '×' };
    default:
      return UNKNOWN_PRESENTATION;
  }
}

/** Aggregate platform health from `/system-health`. */
export function presentSystemStatus(value: unknown): StatusPresentation {
  switch (asString(value)?.toUpperCase()) {
    case 'HEALTHY':
      return { tone: 'positive', label: 'Healthy', glyph: '●' };
    case 'DEGRADED':
      return { tone: 'medium', label: 'Degraded', glyph: '◐' };
    case 'CRITICAL':
      return { tone: 'high', label: 'Critical', glyph: '▲' };
    default:
      return UNKNOWN_PRESENTATION;
  }
}

/** Confidence tokens written by projection, encounter, risk and recommendation processors. */
export function presentConfidence(value: unknown): StatusPresentation {
  switch (asString(value)?.toUpperCase()) {
    case 'HIGH':
      return { tone: 'positive', label: 'High', glyph: '●' };
    case 'MEDIUM':
      return { tone: 'medium', label: 'Medium', glyph: '◐' };
    case 'LOW':
      return { tone: 'low', label: 'Low', glyph: '○' };
    case 'UNKNOWN':
      return UNKNOWN_PRESENTATION;
    default:
      return UNKNOWN_PRESENTATION;
  }
}

/**
 * Geometry / time / altitude overlap flags from the encounter processor.
 *
 * `UNKNOWN` is a first-class stored value — altitude overlap is not evaluated
 * today and is persisted as `UNKNOWN`. This mapper must not promote that
 * token to Yes or No.
 */
export function presentOverlapStatus(value: unknown): StatusPresentation {
  const raw = asString(value);

  if (raw === null) {
    return UNKNOWN_PRESENTATION;
  }

  switch (raw.toUpperCase()) {
    case 'YES':
      return { tone: 'high', label: 'Yes', glyph: '▲' };
    case 'NO':
      return { tone: 'neutral', label: 'No', glyph: '○' };
    case 'UNKNOWN':
      return UNKNOWN_PRESENTATION;
    case 'INSIDE_NOW':
      return { tone: 'high', label: 'Inside hazard now', glyph: '▲' };
    case 'OVERLAP':
      return { tone: 'high', label: 'Overlap', glyph: '◆' };
    case 'NO_OVERLAP':
      return { tone: 'neutral', label: 'No overlap', glyph: '○' };
    default:
      // Other persisted tokens (e.g. CORRIDOR_ONLY_INTERSECTION) stay literal.
      return { tone: 'neutral', label: humaniseToken(raw), glyph: '◆' };
  }
}

/**
 * `inside_now` is a stored boolean, distinct from geometry overlap tokens.
 *
 * Missing stays not-reported. This never treats UNKNOWN as Yes or No because
 * that token is not a boolean value.
 */
export function presentInsideNow(value: unknown): StatusPresentation {
  const flag = asBoolean(value);

  if (flag === true) {
    return { tone: 'high', label: 'Inside hazard now', glyph: '▲' };
  }

  if (flag === false) {
    return { tone: 'neutral', label: 'Not inside now', glyph: '○' };
  }

  return { tone: 'unknown', label: '—', glyph: '?' };
}

/** Stored recommendation action token. Does not invent an instruction. */
export function presentRecommendationAction(value: unknown): StatusPresentation {
  const token = asString(value);

  if (token == null) {
    return UNKNOWN_PRESENTATION;
  }

  return { tone: 'neutral', label: humaniseToken(token), glyph: '▸' };
}

/** Encounter lifecycle states written by the encounter processor. */
export function presentEncounterState(value: unknown): StatusPresentation {
  switch (asString(value)?.toUpperCase()) {
    case 'DETECTED':
      return { tone: 'medium', label: 'Detected', glyph: '◆' };
    case 'MONITORING':
      return { tone: 'neutral', label: 'Monitoring', glyph: '◉' };
    case 'RESOLVED':
      return { tone: 'positive', label: 'Resolved', glyph: '✓' };
    case 'SUPERSEDED':
      return { tone: 'unknown', label: 'Superseded', glyph: '○' };
    case 'EXPIRED':
      return { tone: 'unknown', label: 'Expired', glyph: '○' };
    default:
      return UNKNOWN_PRESENTATION;
  }
}

/** Alert lifecycle states written by the active-alert processor. */
export function presentAlertState(value: unknown): StatusPresentation {
  switch (asString(value)?.toUpperCase()) {
    case 'NEW':
      return { tone: 'high', label: 'New', glyph: '◆' };
    case 'ESCALATED':
      return { tone: 'high', label: 'Escalated', glyph: '▲' };
    case 'UPDATED':
      return { tone: 'medium', label: 'Updated', glyph: '◈' };
    case 'MONITORING':
      return { tone: 'neutral', label: 'Monitoring', glyph: '◉' };
    case 'RESOLVED':
      return { tone: 'positive', label: 'Resolved', glyph: '✓' };
    default:
      return UNKNOWN_PRESENTATION;
  }
}

/** Airport weather impact from the airport-status materializer. */
export function presentWeatherImpact(value: unknown): StatusPresentation {
  switch (asString(value)?.toUpperCase()) {
    case 'WEATHER_IMPACTED':
    case 'IMPACTED':
      return { tone: 'high', label: 'Weather impacted', glyph: '▲' };
    case 'NORMAL':
      return { tone: 'positive', label: 'Normal', glyph: '●' };
    default:
      return UNKNOWN_PRESENTATION;
  }
}

/**
 * Map SIGMET severity onto the shared risk scale.
 *
 * `severity` is an uppercased pass-through string from NOAA rather than a
 * fixed enum (the SIGMET processor does not constrain it), so unrecognised
 * values resolve to `UNKNOWN` instead of being guessed at.
 *
 * This is presentation mapping only: it aligns hazard colouring with the risk
 * colouring used elsewhere and does not assign or infer any risk score.
 */
export function hazardSeverityToRiskLevel(
  value: unknown,
): 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN' {
  switch (asString(value)?.toUpperCase()) {
    case 'SEVERE':
    case 'EXTREME':
      return 'HIGH';
    case 'MODERATE':
      return 'MEDIUM';
    case 'LIGHT':
      return 'LOW';
    default:
      return 'UNKNOWN';
  }
}

/** Severity indicator that shows the source's own wording as the label. */
export function presentHazardSeverity(value: unknown): StatusPresentation {
  const raw = asString(value);

  if (raw === null) {
    return { tone: 'unknown', label: 'Not reported', glyph: '?' };
  }

  return {
    ...presentRiskLevel(hazardSeverityToRiskLevel(raw)),
    label: raw.charAt(0).toUpperCase() + raw.slice(1).toLowerCase(),
  };
}

/** METAR / TAF flight category as stored. Not a risk score. */
export function presentFlightCategory(value: unknown): StatusPresentation {
  switch (asString(value)?.toUpperCase()) {
    case 'LIFR':
      return { tone: 'high', label: 'LIFR', glyph: '▲' };
    case 'IFR':
      return { tone: 'high', label: 'IFR', glyph: '▲' };
    case 'MVFR':
      return { tone: 'medium', label: 'MVFR', glyph: '◆' };
    case 'VFR':
      return { tone: 'positive', label: 'VFR', glyph: '●' };
    default:
      return UNKNOWN_PRESENTATION;
  }
}

/**
 * AirportStatus.assessment_status and AirportAssessment.assessment_status.
 * These are different vocabularies that happen to share a field name.
 */
export function presentAssessmentStatus(value: unknown): StatusPresentation {
  switch (asString(value)?.toUpperCase()) {
    case 'EVALUATED':
      return { tone: 'positive', label: 'Evaluated', glyph: '●' };
    case 'PARTIALLY_EVALUATED':
      return { tone: 'medium', label: 'Partially evaluated', glyph: '◐' };
    case 'WEATHER_PENDING':
      return { tone: 'unknown', label: 'Weather pending', glyph: '?' };
    case 'COMPLETE':
      return { tone: 'positive', label: 'Complete', glyph: '●' };
    case 'WAITING_FOR_WEATHER':
      return { tone: 'unknown', label: 'Waiting for weather', glyph: '?' };
    default:
      return UNKNOWN_PRESENTATION;
  }
}

/** Weather-impact ordering for loaded-page sorts. */
export function weatherImpactRank(value: unknown): number {
  switch (asString(value)?.toUpperCase()) {
    case 'WEATHER_IMPACTED':
    case 'IMPACTED':
      return 3;
    case 'UNKNOWN':
      return 2;
    case 'NORMAL':
      return 1;
    default:
      return 0;
  }
}

/** Ordering helper for lists that should surface the worst case first. */
export function riskRank(value: unknown): number {
  switch (asString(value)?.toUpperCase()) {
    case 'HIGH':
      return 4;
    case 'MEDIUM':
      return 3;
    case 'LOW':
      return 2;
    case 'UNKNOWN':
      return 1;
    default:
      return 0;
  }
}
