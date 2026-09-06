import type { GeoJSONSource, Map as MapLibreMap } from 'maplibre-gl';
import { useEffect } from 'react';

import { MAP_IDS } from './mapStyle';
import {
  EMPTY_HAZARD_COLLECTION,
  type HazardFeatureCollection,
} from './hazardGeoJson';

/**
 * Render active hazard polygons as a GeoJSON source with fill and outline
 * layers.
 *
 * The source is added once and updated in place via `setData` on every poll.
 * Removing and re-adding layers each cycle would cause a visible flash and
 * discard MapLibre's tiling of the geometry.
 *
 * This is a WebGL-backed data source rather than per-hazard DOM markers, which
 * is the same approach the aircraft layer will use.
 */
export function useHazardLayer(
  map: MapLibreMap | null,
  ready: boolean,
  collection: HazardFeatureCollection,
  onSelectHazard: (hazardId: string) => void,
): void {
  // Create the source and layers once the style is available.
  useEffect(() => {
    if (map === null || !ready) {
      return;
    }

    if (map.getSource(MAP_IDS.hazardSource) === undefined) {
      map.addSource(MAP_IDS.hazardSource, {
        type: 'geojson',
        data: EMPTY_HAZARD_COLLECTION,
      });
    }

    // `severityRank` mirrors the shared risk scale (4 high .. 1 unknown) so
    // hazard fills match risk colouring elsewhere in the console. Severity is
    // classified in hazardGeoJson.ts; no scoring happens here.
    const severityColour = [
      'match',
      ['get', 'severityRank'],
      4,
      '#ff6a55',
      3,
      '#f0b429',
      2,
      '#3fbf87',
      '#7c8ba1',
    ] as unknown as string;

    if (map.getLayer(MAP_IDS.hazardFill) === undefined) {
      map.addLayer({
        id: MAP_IDS.hazardFill,
        type: 'fill',
        source: MAP_IDS.hazardSource,
        paint: {
          'fill-color': severityColour,
          'fill-opacity': [
            'case',
            ['==', ['get', 'emphasized'], true],
            0.34,
            0.16,
          ],
        },
      });
    }

    if (map.getLayer(MAP_IDS.hazardOutline) === undefined) {
      map.addLayer({
        id: MAP_IDS.hazardOutline,
        type: 'line',
        source: MAP_IDS.hazardSource,
        paint: {
          'line-color': severityColour,
          'line-width': [
            'case',
            ['==', ['get', 'emphasized'], true],
            2.6,
            1.2,
          ],
          'line-opacity': [
            'case',
            ['==', ['get', 'emphasized'], true],
            1,
            0.85,
          ],
        },
      });
    }
  }, [map, ready]);

  // Push the latest hazard geometry without rebuilding the layers.
  useEffect(() => {
    if (map === null || !ready) {
      return;
    }

    const source = map.getSource(MAP_IDS.hazardSource) as
      | GeoJSONSource
      | undefined;

    source?.setData(collection);
  }, [map, ready, collection]);

  // Selection and pointer affordance.
  useEffect(() => {
    if (map === null || !ready) {
      return;
    }

    const handleClick = (event: {
      features?: Array<{ properties?: Record<string, unknown> | null }>;
    }) => {
      const hazardId = event.features?.[0]?.properties?.hazardId;

      if (typeof hazardId === 'string') {
        onSelectHazard(hazardId);
      }
    };

    const showPointer = () => {
      map.getCanvas().style.cursor = 'pointer';
    };

    const hidePointer = () => {
      map.getCanvas().style.cursor = '';
    };

    map.on('click', MAP_IDS.hazardFill, handleClick);
    map.on('mouseenter', MAP_IDS.hazardFill, showPointer);
    map.on('mouseleave', MAP_IDS.hazardFill, hidePointer);

    return () => {
      map.off('click', MAP_IDS.hazardFill, handleClick);
      map.off('mouseenter', MAP_IDS.hazardFill, showPointer);
      map.off('mouseleave', MAP_IDS.hazardFill, hidePointer);
    };
  }, [map, ready, onSelectHazard]);
}
