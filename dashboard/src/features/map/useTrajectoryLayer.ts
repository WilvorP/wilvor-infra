import type { GeoJSONSource, Map as MapLibreMap } from 'maplibre-gl';
import { useEffect } from 'react';

import type { AircraftProjectionPoint } from '@/types/api';

import { MAP_IDS } from './mapStyle';
import {
  EMPTY_PROJECTION_COLLECTION,
  projectionCollectionForSelection,
} from './projectionGeoJson';

/** Matches `--wv-accent-strong`; trajectory is selection context, not risk. */
const TRAJECTORY_COLOUR = '#5cc3f2';

/**
 * Draw the selected aircraft's short-term projected trajectory.
 *
 * One GeoJSON source, updated with `setData`. The layer is not fitted to the
 * operator's view — continuous `fitBounds` would fight manual pan and zoom.
 * Clearing the selection (or receiving no usable points) writes an empty
 * collection so the line disappears without tearing down the source.
 */
export function useTrajectoryLayer(
  map: MapLibreMap | null,
  ready: boolean,
  selected: boolean,
  points: readonly AircraftProjectionPoint[] | null | undefined,
): void {
  useEffect(() => {
    if (map === null || !ready) {
      return;
    }

    if (map.getSource(MAP_IDS.trajectorySource) === undefined) {
      map.addSource(MAP_IDS.trajectorySource, {
        type: 'geojson',
        data: EMPTY_PROJECTION_COLLECTION,
      });
    }

    if (map.getLayer(MAP_IDS.trajectoryLine) === undefined) {
      map.addLayer({
        id: MAP_IDS.trajectoryLine,
        type: 'line',
        source: MAP_IDS.trajectorySource,
        filter: ['==', ['geometry-type'], 'LineString'],
        paint: {
          'line-color': TRAJECTORY_COLOUR,
          'line-width': 2.2,
          'line-opacity': 0.9,
        },
      });
    }

    if (map.getLayer(MAP_IDS.trajectoryPoints) === undefined) {
      map.addLayer({
        id: MAP_IDS.trajectoryPoints,
        type: 'circle',
        source: MAP_IDS.trajectorySource,
        filter: ['==', ['geometry-type'], 'Point'],
        paint: {
          'circle-radius': 3.2,
          'circle-color': TRAJECTORY_COLOUR,
          'circle-stroke-width': 1,
          'circle-stroke-color': '#080b11',
        },
      });
    }
  }, [map, ready]);

  useEffect(() => {
    if (map === null || !ready) {
      return;
    }

    const source = map.getSource(MAP_IDS.trajectorySource) as
      | GeoJSONSource
      | undefined;

    source?.setData(projectionCollectionForSelection(selected, points));
  }, [map, ready, selected, points]);
}
