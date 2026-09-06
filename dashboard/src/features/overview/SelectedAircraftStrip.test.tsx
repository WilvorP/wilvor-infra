import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { SelectedAircraftStrip } from './SelectedAircraftStrip';

describe('SelectedAircraftStrip', () => {
  it('keeps Overview on the projection and links to Aircraft Investigation', () => {
    render(
      <MemoryRouter>
        <SelectedAircraftStrip
          selection={{
            aircraftId: 'aa0001',
            encounterId: 'enc-2',
            source: 'encounter',
          }}
          aircraft={{
            aircraftId: 'aa0001',
            callsign: 'UAL9',
            longitude: -122.3,
            latitude: 37.6,
            trackDeg: 270,
            baroAltitudeFt: 35000,
            groundSpeedKt: 430,
            positionTimeEpoch: 1786515880,
          }}
          hasProjection
          onClear={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(
      screen.getByText('Current projection shown on the map.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Current operational contexts')).not.toBeInTheDocument();

    const link = screen.getByRole('link', { name: 'Open investigation' });

    expect(link.getAttribute('href')).toContain('/aircraft/aa0001');
    expect(link.getAttribute('href')).toContain('encounterId=enc-2');
  });
});
