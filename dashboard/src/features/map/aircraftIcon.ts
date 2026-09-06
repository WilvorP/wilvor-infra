/**
 * Procedurally generated aircraft symbol for the map's icon layer.
 *
 * Rasterised into an RGBA buffer rather than drawn on a `<canvas>` or shipped
 * as an asset for three reasons: MapLibre's `addImage` takes raw RGBA anyway,
 * it keeps the symbol testable without a DOM, and it avoids a second network
 * request before the traffic picture can render.
 *
 * The shape points north (up). MapLibre's `icon-rotate` turns clockwise from
 * north, which is the same convention as the `track_deg` true track the
 * platform records, so the property maps onto the paint property directly with
 * no angle conversion.
 */

export interface IconImage {
  readonly width: number;
  readonly height: number;
  /** Non-premultiplied RGBA, row-major from the top-left. */
  readonly data: Uint8Array;
}

type Point = readonly [number, number];

/**
 * Dart outline in normalised coordinates: x right, y down, nose at the top.
 *
 * Kept inside ±0.78 so the outline pass, which scales outwards, still fits
 * the bitmap without clipping.
 */
const AIRCRAFT_SHAPE: readonly Point[] = [
  [0, -0.78],
  [0.56, 0.6],
  [0, 0.25],
  [-0.56, 0.6],
];

/** Scale of the dark casing drawn behind the symbol. */
const OUTLINE_SCALE = 1.28;

/**
 * Pale blue. Distinct from the amber/red used for hazard severity and risk so
 * aircraft never read as carrying a severity of their own — this milestone
 * deliberately applies no risk colouring.
 */
export const AIRCRAFT_FILL_RGB: readonly [number, number, number] = [
  207, 227, 255,
];

/** Matches `--wv-bg-base`, so the symbol stays legible over hazard fills. */
const AIRCRAFT_OUTLINE_RGB: readonly [number, number, number] = [8, 11, 17];

const OUTLINE_ALPHA = 0.88;

function pointInPolygon(x: number, y: number, polygon: readonly Point[]) {
  let inside = false;

  for (
    let current = 0, previous = polygon.length - 1;
    current < polygon.length;
    previous = current++
  ) {
    const [xi, yi] = polygon[current]!;
    const [xj, yj] = polygon[previous]!;

    const straddlesRay = yi > y !== yj > y;

    if (straddlesRay && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }

  return inside;
}

/**
 * Per-pixel coverage in 0..1, supersampled so edges are antialiased.
 *
 * Without this the symbol has hard stair-stepped edges that read as visual
 * noise once several thousand of them are on screen.
 */
function rasterise(
  polygon: readonly Point[],
  size: number,
  samples: number,
): Float32Array {
  const coverage = new Float32Array(size * size);
  const step = 1 / (samples + 1);

  for (let py = 0; py < size; py += 1) {
    for (let px = 0; px < size; px += 1) {
      let hits = 0;

      for (let sy = 1; sy <= samples; sy += 1) {
        for (let sx = 1; sx <= samples; sx += 1) {
          const nx = ((px + sx * step) / size) * 2 - 1;
          const ny = ((py + sy * step) / size) * 2 - 1;

          if (pointInPolygon(nx, ny, polygon)) {
            hits += 1;
          }
        }
      }

      coverage[py * size + px] = hits / (samples * samples);
    }
  }

  return coverage;
}

function scalePolygon(
  polygon: readonly Point[],
  factor: number,
): readonly Point[] {
  return polygon.map(([x, y]) => [x * factor, y * factor] as Point);
}

export function createAircraftIcon(size = 32, samples = 3): IconImage {
  const fill = rasterise(AIRCRAFT_SHAPE, size, samples);
  const outline = rasterise(
    scalePolygon(AIRCRAFT_SHAPE, OUTLINE_SCALE),
    size,
    samples,
  );

  const data = new Uint8Array(size * size * 4);

  for (let index = 0; index < size * size; index += 1) {
    const fillAlpha = fill[index]!;
    const outlineAlpha = outline[index]! * OUTLINE_ALPHA;

    // Source-over: fill above casing. Composited manually because the buffer
    // is non-premultiplied and MapLibre expects it that way.
    const alpha = fillAlpha + outlineAlpha * (1 - fillAlpha);

    if (alpha <= 0) {
      continue;
    }

    const offset = index * 4;

    for (let channel = 0; channel < 3; channel += 1) {
      const blended =
        AIRCRAFT_FILL_RGB[channel]! * fillAlpha +
        AIRCRAFT_OUTLINE_RGB[channel]! * outlineAlpha * (1 - fillAlpha);

      data[offset + channel] = Math.round(blended / alpha);
    }

    data[offset + 3] = Math.round(alpha * 255);
  }

  return { width: size, height: size, data };
}
