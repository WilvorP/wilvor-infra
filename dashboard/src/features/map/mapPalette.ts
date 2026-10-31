/**
 * Map paint colours.
 *
 * WebGL paint properties cannot read CSS custom properties, so the values in
 * styles/tokens.css that the map needs are mirrored here once rather than
 * re-typed in each layer hook.
 */

/** Map background beneath the raster basemap. */
export const MAP_BACKGROUND = '#1e2732';

/** Dark casing around symbols so they stay legible over hazard fills. */
export const MAP_CASING = '#131820';

/** Matches `--wv-accent`; selection highlight only. */
export const MAP_SELECTION = '#5f9fe0';

/** Matches `--wv-accent-strong`; trajectory is selection context, not risk. */
export const MAP_TRAJECTORY = '#86b8ed';

/** Matches `--wv-risk-*`, ordered by the shared rank (4 high .. 1 unknown). */
export const MAP_RISK_COLOURS = {
  high: '#ef6c5c',
  medium: '#e2aa40',
  low: '#4fbb8b',
  unknown: '#8d98a8',
} as const;

/** MapLibre `match` expression from a 4..1 rank property to a risk colour. */
export function riskRankColour(property: string): string {
  return [
    'match',
    ['get', property],
    4,
    MAP_RISK_COLOURS.high,
    3,
    MAP_RISK_COLOURS.medium,
    2,
    MAP_RISK_COLOURS.low,
    MAP_RISK_COLOURS.unknown,
  ] as unknown as string;
}
