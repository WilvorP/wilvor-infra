import { MemoryRouter } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { EvidencePanel } from './EvidencePanel';
import {
  emptyEvidenceResult,
  evidenceDisplayResult,
} from './fixtures';

describe('EvidencePanel', () => {
  it('waits for a result without inventing aircraft or hazard rows', () => {
    render(
      <MemoryRouter>
        <EvidencePanel result={null} />
      </MemoryRouter>,
    );

    expect(screen.getByTestId('copilot-evidence-idle')).toBeInTheDocument();
    expect(screen.queryByText('UAL9')).not.toBeInTheDocument();
  });

  it('does not treat an empty page as a verified-zero or impacted count', () => {
    render(
      <MemoryRouter>
        <EvidencePanel result={emptyEvidenceResult()} />
      </MemoryRouter>,
    );

    expect(
      screen.getByText(/not a verified-zero claim/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/0 aircraft/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/verified zero/i)).not.toBeInTheDocument();
  });

  it('renders injected aircraft and hazard evidence with distinct clocks', () => {
    render(
      <MemoryRouter>
        <EvidencePanel result={evidenceDisplayResult()} />
      </MemoryRouter>,
    );

    expect(screen.getAllByText('UAL9').length).toBeGreaterThan(0);
    expect(screen.getAllByText('sigmet-a').length).toBeGreaterThan(0);
    expect(screen.getByText('wilvor_aircraft_current_state')).toBeInTheDocument();
    expect(screen.getByText('v-aircraft-17')).toBeInTheDocument();
    expect(screen.getAllByText(/2026-11-07 17:59:40Z/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/2026-11-07 18:00:05Z/).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/Source authenticity: not established/),
    ).toBeInTheDocument();
    const investigationLinks = screen.getAllByRole('link', {
      name: 'Open aircraft investigation',
    });
    expect(investigationLinks.length).toBeGreaterThan(0);
    expect(investigationLinks[0]).toHaveAttribute('href', '/aircraft/aa0001');
    expect(
      screen.getByText(/truncated or has additional pages/i),
    ).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });
});
