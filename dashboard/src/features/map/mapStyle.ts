import type { StyleSpecification } from 'maplibre-gl';

/**
 * Built-in dark basemap.
 *
 * Defined inline as a raster style rather than fetched from a vendor style
 * endpoint so the console has no extra request before the first paint.
 * Override with `VITE_WILVOR_MAP_STYLE_URL` to point at a self-hosted or
 * vendor style.
 *
 * CARTO raster tiles accept `VITE_CARTO_BASEMAP_KEY` as `key`. Without
 * it the tiles still load but watermark "API KEY REQUIRED".
 *
 * This style intentionally declares no `glyphs` source, so symbol layers
 * cannot render text. The aircraft layer therefore uses a rotated icon and a
 * circle, neither of which needs glyphs; adding callsign labels to the map
 * would require a glyph endpoint first.
 */

const CARTO_DARK_SUBDOMAINS = ['a', 'b', 'c', 'd'] as const;

const CARTO_ATTRIBUTION =
  '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap</a> contributors, <a href="https://carto.com/attributions">© CARTO</a>';

export type RawCartoEnv = Record<string, string | boolean | undefined>;

export function readCartoBasemapKey(env: RawCartoEnv): string | null {
  const value = env.VITE_CARTO_BASEMAP_KEY;

  if (typeof value !== 'string') {
    return null;
  }

  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

export function cartoDarkTileUrl(
  subdomain: string,
  apiKey: string | null,
): string {
  const base = `https://${subdomain}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png`;

  if (apiKey === null) {
    return base;
  }

  return `${base}?key=${encodeURIComponent(apiKey)}`;
}

export function buildDarkBasemapStyle(
  apiKey: string | null,
): StyleSpecification {
  return {
    version: 8,
    sources: {
      basemap: {
        type: 'raster',
        tiles: CARTO_DARK_SUBDOMAINS.map((subdomain) =>
          cartoDarkTileUrl(subdomain, apiKey),
        ),
        tileSize: 256,
        maxzoom: 19,
        attribution: CARTO_ATTRIBUTION,
      },
    },
    layers: [
      {
        id: 'background',
        type: 'background',
        paint: { 'background-color': '#080b11' },
      },
      {
        id: 'basemap',
        type: 'raster',
        source: 'basemap',
        paint: {
          // Desaturated and dimmed so operational overlays stay dominant.
          'raster-opacity': 0.72,
          'raster-saturation': -0.35,
        },
      },
    ],
  };
}

const resolvedCartoKey = readCartoBasemapKey(
  import.meta.env as unknown as RawCartoEnv,
);

if (resolvedCartoKey === null && import.meta.env.DEV) {
  console.warn(
    'VITE_CARTO_BASEMAP_KEY is not set. CARTO tiles may watermark API KEY REQUIRED. Add the key to dashboard/.env.local.',
  );
}

export const DARK_BASEMAP_STYLE: StyleSpecification =
  buildDarkBasemapStyle(resolvedCartoKey);

/** Continental US default view, matching the OpenSky polling bounding box. */
export const DEFAULT_VIEW = {
  center: [-98.5, 39.5] as [number, number],
  zoom: 3.4,
} as const;

/** Source, layer and image identifiers, kept together to avoid string drift. */
export const MAP_IDS = {
  hazardSource: 'wilvor-hazards',
  hazardFill: 'wilvor-hazards-fill',
  hazardOutline: 'wilvor-hazards-outline',

  aircraftSource: 'wilvor-aircraft',
  /** Registered via `addImage`, not part of the style document. */
  aircraftIconImage: 'wilvor-aircraft-icon',
  /** Selection highlight, drawn beneath the symbols. */
  aircraftHalo: 'wilvor-aircraft-halo',
  /** Aircraft with a reported track: rotated icon. */
  aircraftSymbol: 'wilvor-aircraft-symbol',
  /** Aircraft with no reported track: plain dot, no implied heading. */
  aircraftDot: 'wilvor-aircraft-dot',

  /** Selected-aircraft short-term motion projection. */
  trajectorySource: 'wilvor-projection',
  trajectoryLine: 'wilvor-projection-line',
  trajectoryPoints: 'wilvor-projection-points',

  airportSource: 'wilvor-airports',
  airportHalo: 'wilvor-airports-halo',
  airportCircle: 'wilvor-airports-circle',
} as const;
