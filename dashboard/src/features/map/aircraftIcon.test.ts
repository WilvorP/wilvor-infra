import { describe, expect, it } from 'vitest';

import { createAircraftIcon } from './aircraftIcon';

const SIZE = 32;

function alphaAt(
  icon: ReturnType<typeof createAircraftIcon>,
  x: number,
  y: number,
): number {
  return icon.data[(y * icon.width + x) * 4 + 3]!;
}

describe('createAircraftIcon', () => {
  it('produces an RGBA buffer of the requested size', () => {
    const icon = createAircraftIcon(SIZE);

    expect(icon.width).toBe(SIZE);
    expect(icon.height).toBe(SIZE);
    expect(icon.data).toHaveLength(SIZE * SIZE * 4);
  });

  it('points north, so icon-rotate can take the track directly', () => {
    const icon = createAircraftIcon(SIZE);
    const centre = SIZE / 2;

    // Solid near the nose at the top of the bitmap...
    expect(alphaAt(icon, centre, 3)).toBeGreaterThan(200);

    // ...and empty at the tail notch, which is what makes the direction
    // readable rather than looking like a plain triangle.
    expect(alphaAt(icon, centre, SIZE - 2)).toBe(0);
  });

  it('is symmetric about its vertical axis', () => {
    // An asymmetric bitmap would appear to wobble as it rotates.
    const icon = createAircraftIcon(SIZE);

    for (let y = 0; y < SIZE; y += 1) {
      for (let x = 0; x < SIZE / 2; x += 1) {
        expect(alphaAt(icon, x, y)).toBe(alphaAt(icon, SIZE - 1 - x, y));
      }
    }
  });

  it('leaves the corners fully transparent', () => {
    const icon = createAircraftIcon(SIZE);

    for (const [x, y] of [
      [0, 0],
      [SIZE - 1, 0],
      [0, SIZE - 1],
      [SIZE - 1, SIZE - 1],
    ]) {
      expect(alphaAt(icon, x!, y!)).toBe(0);
    }
  });

  it('antialiases its edges', () => {
    // Several thousand hard-edged symbols read as visual noise.
    const icon = createAircraftIcon(SIZE);

    let partial = 0;

    for (let index = 3; index < icon.data.length; index += 4) {
      const alpha = icon.data[index]!;

      if (alpha > 0 && alpha < 255) {
        partial += 1;
      }
    }

    expect(partial).toBeGreaterThan(0);
  });
});
