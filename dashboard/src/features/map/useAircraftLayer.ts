import type {
  FilterSpecification,
  GeoJSONSource,
  Map as MapLibreMap,
} from 'maplibre-gl';
import { useEffect } from 'react';

import {
  EMPTY_AIRCRAFT_COLLECTION,
  type AircraftFeatureCollection,
} from './aircraftGeoJson';
import { AIRCRAFT_FILL_RGB, createAircraftIcon } from './aircraftIcon';
import { MAP_IDS } from './mapStyle';

const AIRCRAFT_COLOUR = `rgb(${AIRCRAFT_FILL_RGB.join(', ')})`;

/** Matches `--wv-accent`; used only for the selection highlight. */
const SELECTION_COLOUR = '#3ba9dd';

/** Matches `--wv-bg-base`, giving symbols a dark casing over hazard fills. */
const CASING_COLOUR = '#080b11';

/**
 * The bitmap is generated at twice its display size and registered with
 * `pixelRatio: 2`, so it stays sharp on high-DPI displays without doubling
 * the on-screen footprint.
 */
const ICON_BITMAP_SIZE = 32;

/**
 * Render the aircraft layer from `GET /map/aircraft`.
 *
 * This is a single WebGL-backed GeoJSON source, not per-aircraft DOM markers.
 * Several thousand `Marker` instances would each be an absolutely positioned
 * element repositioned on every frame of every pan, which does not stay
 * interactive at network scale.
 *
 * Three layers share the one source:
 *   - a selection halo, filtered to the selected aircraft
 *   - a plain dot for aircraft with no reported track
 *   - a rotated icon for aircraft with a track
 *
 * Splitting on heading availability is deliberate. Rendering an arrow for an
 * aircraft whose track the platform never received would assert a heading
 * that does not exist, so those aircraft are drawn as directionless dots.
 *
 * Risk colouring is intentionally absent in this milestone: risk level is not
 * part of the `/map/aircraft` projection, and inferring it in the browser
 * would duplicate backend scoring.
 */
export function useAircraftLayer(
  map: MapLibreMap | null,
  ready: boolean,
  collection: AircraftFeatureCollection,
  selectedAircraftId: string | null,
  onSelectAircraft: (aircraftId: string) => void,
): void {
  // Register the icon and create the source and layers once.
  useEffect(() => {
    if (map === null || !ready) {
      return;
    }

    if (!map.hasImage(MAP_IDS.aircraftIconImage)) {
      map.addImage(
        MAP_IDS.aircraftIconImage,
        createAircraftIcon(ICON_BITMAP_SIZE),
        { pixelRatio: 2 },
      );
    }

    if (map.getSource(MAP_IDS.aircraftSource) === undefined) {
      map.addSource(MAP_IDS.aircraftSource, {
        type: 'geojson',
        data: EMPTY_AIRCRAFT_COLLECTION,
      });
    }

    if (map.getLayer(MAP_IDS.aircraftHalo) === undefined) {
      map.addLayer({
        id: MAP_IDS.aircraftHalo,
        type: 'circle',
        source: MAP_IDS.aircraftSource,
        // Nothing is selected initially; a later effect narrows this.
        filter: ['==', ['get', 'aircraftId'], ''] as FilterSpecification,
        paint: {
          'circle-radius': [
            'interpolate',
            ['linear'],
            ['zoom'],
            3,
            10,
            10,
            16,
          ],
          'circle-color': SELECTION_COLOUR,
          'circle-opacity': 0.18,
          'circle-stroke-width': 1.5,
          'circle-stroke-color': SELECTION_COLOUR,
          'circle-stroke-opacity': 0.9,
        },
      });
    }

    if (map.getLayer(MAP_IDS.aircraftDot) === undefined) {
      map.addLayer({
        id: MAP_IDS.aircraftDot,
        type: 'circle',
        source: MAP_IDS.aircraftSource,
        filter: ['==', ['get', 'hasTrack'], false] as FilterSpecification,
        paint: {
          'circle-radius': [
            'interpolate',
            ['linear'],
            ['zoom'],
            3,
            2.2,
            10,
            4,
          ],
          'circle-color': AIRCRAFT_COLOUR,
          'circle-stroke-width': 1,
          'circle-stroke-color': CASING_COLOUR,
        },
      });
    }

    if (map.getLayer(MAP_IDS.aircraftSymbol) === undefined) {
      map.addLayer({
        id: MAP_IDS.aircraftSymbol,
        type: 'symbol',
        source: MAP_IDS.aircraftSource,
        filter: ['==', ['get', 'hasTrack'], true] as FilterSpecification,
        layout: {
          'icon-image': MAP_IDS.aircraftIconImage,
          // `track_deg` is degrees true clockwise from north, which is exactly
          // what icon-rotate expects. No conversion is applied.
          'icon-rotate': ['get', 'trackDeg'],
          'icon-rotation-alignment': 'map',
          // Collision detection would hide most of the fleet in busy airspace
          // and costs placement work on every poll.
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          'icon-size': [
            'interpolate',
            ['linear'],
            ['zoom'],
            3,
            0.55,
            6,
            0.75,
            10,
            1,
          ],
        },
      });
    }
  }, [map, ready]);

  // Push the latest positions without rebuilding the layers, so the operator's
  // pan, zoom and current selection all survive every poll.
  useEffect(() => {
    if (map === null || !ready) {
      return;
    }

    const source = map.getSource(MAP_IDS.aircraftSource) as
      | GeoJSONSource
      | undefined;

    source?.setData(collection);
  }, [map, ready, collection]);

  // Narrow the halo to the selected aircraft.
  useEffect(() => {
    if (map === null || !ready) {
      return;
    }

    if (map.getLayer(MAP_IDS.aircraftHalo) === undefined) {
      return;
    }

    map.setFilter(MAP_IDS.aircraftHalo, [
      '==',
      ['get', 'aircraftId'],
      // `aircraftId` is always a non-empty string, so an empty comparand
      // matches no feature and hides the halo.
      selectedAircraftId ?? '',
    ] as FilterSpecification);
  }, [map, ready, selectedAircraftId]);

  // Selection and pointer affordance on both aircraft representations.
  useEffect(() => {
    if (map === null || !ready) {
      return;
    }

    const handleClick = (event: {
      features?: Array<{ properties?: Record<string, unknown> | null }>;
    }) => {
      const aircraftId = event.features?.[0]?.properties?.aircraftId;

      if (typeof aircraftId === 'string' && aircraftId.length > 0) {
        onSelectAircraft(aircraftId);
      }
    };

    const showPointer = () => {
      map.getCanvas().style.cursor = 'pointer';
    };

    const hidePointer = () => {
      map.getCanvas().style.cursor = '';
    };

    const layers = [MAP_IDS.aircraftSymbol, MAP_IDS.aircraftDot];

    for (const layer of layers) {
      map.on('click', layer, handleClick);
      map.on('mouseenter', layer, showPointer);
      map.on('mouseleave', layer, hidePointer);
    }

    return () => {
      for (const layer of layers) {
        map.off('click', layer, handleClick);
        map.off('mouseenter', layer, showPointer);
        map.off('mouseleave', layer, hidePointer);
      }
    };
  }, [map, ready, onSelectAircraft]);
}
