import type {
  FilterSpecification,
  GeoJSONSource,
  Map as MapLibreMap,
} from 'maplibre-gl';
import { useEffect } from 'react';

import { MAP_IDS } from './mapStyle';
import {
  EMPTY_AIRPORT_COLLECTION,
  type AirportFeatureCollection,
} from './airportGeoJson';

const RISK_COLOUR = [
  'match',
  ['get', 'riskRank'],
  4,
  '#ff6a55',
  3,
  '#f0b429',
  2,
  '#3fbf87',
  '#7c8ba1',
] as unknown as string;

const SELECTION_COLOUR = '#3ba9dd';

/**
 * Airport markers from loaded `AirportStatus` pages.
 *
 * One GeoJSON source, updated with `setData`. Colour is supplementary to the
 * stored weather-risk text shown in the worklist.
 */
export function useAirportLayer(
  map: MapLibreMap | null,
  ready: boolean,
  collection: AirportFeatureCollection,
  selectedAirportId: string | null,
  onSelectAirport: ((airportId: string) => void) | null,
): void {
  useEffect(() => {
    if (map === null || !ready) {
      return;
    }

    if (map.getSource(MAP_IDS.airportSource) === undefined) {
      map.addSource(MAP_IDS.airportSource, {
        type: 'geojson',
        data: EMPTY_AIRPORT_COLLECTION,
      });
    }

    if (map.getLayer(MAP_IDS.airportHalo) === undefined) {
      map.addLayer({
        id: MAP_IDS.airportHalo,
        type: 'circle',
        source: MAP_IDS.airportSource,
        filter: ['==', ['get', 'airportId'], ''] as FilterSpecification,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 8, 10, 14],
          'circle-color': SELECTION_COLOUR,
          'circle-opacity': 0.2,
          'circle-stroke-width': 1.5,
          'circle-stroke-color': SELECTION_COLOUR,
        },
      });
    }

    if (map.getLayer(MAP_IDS.airportCircle) === undefined) {
      map.addLayer({
        id: MAP_IDS.airportCircle,
        type: 'circle',
        source: MAP_IDS.airportSource,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 3.2, 10, 6],
          'circle-color': RISK_COLOUR,
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

    const source = map.getSource(MAP_IDS.airportSource) as
      | GeoJSONSource
      | undefined;

    source?.setData(collection);
  }, [map, ready, collection]);

  useEffect(() => {
    if (map === null || !ready || map.getLayer(MAP_IDS.airportHalo) === undefined) {
      return;
    }

    map.setFilter(MAP_IDS.airportHalo, [
      '==',
      ['get', 'airportId'],
      selectedAirportId ?? '',
    ] as FilterSpecification);
  }, [map, ready, selectedAirportId]);

  useEffect(() => {
    if (map === null || !ready || onSelectAirport == null) {
      return;
    }

    const handleClick = (event: {
      features?: Array<{ properties?: Record<string, unknown> | null }>;
    }) => {
      const airportId = event.features?.[0]?.properties?.airportId;

      if (typeof airportId === 'string' && airportId.length > 0) {
        onSelectAirport(airportId);
      }
    };

    const showPointer = () => {
      map.getCanvas().style.cursor = 'pointer';
    };

    const hidePointer = () => {
      map.getCanvas().style.cursor = '';
    };

    map.on('click', MAP_IDS.airportCircle, handleClick);
    map.on('mouseenter', MAP_IDS.airportCircle, showPointer);
    map.on('mouseleave', MAP_IDS.airportCircle, hidePointer);

    return () => {
      map.off('click', MAP_IDS.airportCircle, handleClick);
      map.off('mouseenter', MAP_IDS.airportCircle, showPointer);
      map.off('mouseleave', MAP_IDS.airportCircle, hidePointer);
    };
  }, [map, ready, onSelectAirport]);
}
