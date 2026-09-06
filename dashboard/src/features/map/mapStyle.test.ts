import { describe, expect, it } from 'vitest';

import {
  buildDarkBasemapStyle,
  cartoDarkTileUrl,
  readCartoBasemapKey,
} from './mapStyle';

describe('CARTO basemap key', () => {
  it('reads a present key and treats blanks as absent', () => {
    expect(readCartoBasemapKey({ VITE_CARTO_BASEMAP_KEY: 'test-key' })).toBe(
      'test-key',
    );
    expect(readCartoBasemapKey({ VITE_CARTO_BASEMAP_KEY: '   ' })).toBeNull();
    expect(readCartoBasemapKey({})).toBeNull();
  });

  it('keeps the unauthenticated tile URL when the key is absent', () => {
    expect(cartoDarkTileUrl('a', null)).toBe(
      'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
    );
    expect(cartoDarkTileUrl('a', null)).not.toContain('key=');
  });

  it('appends an encoded key query parameter without inventing a default', () => {
    expect(cartoDarkTileUrl('b', 'key/value')).toBe(
      'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png?key=key%2Fvalue',
    );
  });

  it('builds the shared style with or without a key', () => {
    const withKey = buildDarkBasemapStyle('unit-test-key');
    const source = withKey.sources.basemap;

    expect(source.type).toBe('raster');
    if (source.type !== 'raster') {
      return;
    }

    expect(source.tiles).toEqual([
      'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png?key=unit-test-key',
      'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png?key=unit-test-key',
      'https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png?key=unit-test-key',
      'https://d.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png?key=unit-test-key',
    ]);
    expect(source.attribution).toContain('© OpenStreetMap');
    expect(source.attribution).toContain('© CARTO');

    const withoutKey = buildDarkBasemapStyle(null);
    const bare = withoutKey.sources.basemap;
    if (bare.type !== 'raster') {
      throw new Error('expected raster source');
    }

    expect(bare.tiles?.every((tile) => !tile.includes('key='))).toBe(true);
  });
});
