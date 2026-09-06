/**
 * Narrow coercion helpers for the API boundary.
 *
 * The operational API returns raw DynamoDB items whose attributes are omitted
 * rather than nulled when absent, so any field can be missing. Rather than
 * adding a schema-validation dependency for records the UI only partially
 * reads, these helpers make "absent or wrong type" collapse to a single
 * `null`, which components can render as an explicit "not reported" state.
 */

/** A finite number, or `null` for absent, null, NaN and Infinity. */
export function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** A non-empty trimmed string, or `null`. */
export function asString(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null;
  }

  const trimmed = value.trim();

  return trimmed.length > 0 ? trimmed : null;
}

export function asBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

/** Always an array, so callers can map without a null guard. */
export function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

/** Array of non-empty strings, discarding non-string entries. */
export function asStringArray(value: unknown): string[] {
  return asArray<unknown>(value)
    .map(asString)
    .filter((entry): entry is string => entry !== null);
}

/** A `Record<string, number>` counter map, discarding non-numeric entries. */
export function asCountMap(value: unknown): Record<string, number> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return {};
  }

  const result: Record<string, number> = {};

  for (const [key, entry] of Object.entries(value)) {
    const count = asNumber(entry);

    if (count !== null) {
      result[key] = count;
    }
  }

  return result;
}

/** Valid WGS84 coordinate pair, or `null` if either component is unusable. */
export function asCoordinate(
  longitude: unknown,
  latitude: unknown,
): [number, number] | null {
  const lon = asNumber(longitude);
  const lat = asNumber(latitude);

  if (lon === null || lat === null) {
    return null;
  }

  if (lon < -180 || lon > 180 || lat < -90 || lat > 90) {
    return null;
  }

  return [lon, lat];
}
