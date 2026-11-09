import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { CopilotQueryClient } from '@/api/aiClient';
import { createInjectedCopilotQueryClient } from '@/api/aiClient';
import {
  assertCopilotQueryResult,
  LOCAL_DEV_PREVIEW_STANDING,
  MASTER_RESPONSE_STATUS_TEXT,
} from '@/api/aiContracts';
import { ApiError } from '@/api/errors';
import { renderWithProviders } from '@/test/renderWithProviders';

import { CopilotPage } from './CopilotPage';
import {
  AIRCRAFT_EVIDENCE,
  developerTool,
  encounterTool,
  evidenceDisplayResult,
  localPreviewResult,
  TEST_APPLICATION_TIME,
  TEST_SESSION,
  unavailableResult,
  unsupportedResult,
} from './fixtures';

function renderCopilot(
  client = createInjectedCopilotQueryClient(async (request) =>
    unavailableResult(request),
  ),
  path = '/copilot',
) {
  let seq = 0;

  return renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <CopilotPage
        createSession={() => TEST_SESSION}
        createRequestId={() => `id${seq++}`}
        now={() => new Date(TEST_APPLICATION_TIME)}
      />
    </MemoryRouter>,
    { aiClient: client },
  );
}

async function ask(text: string) {
  fireEvent.change(screen.getByLabelText('Operator question'), {
    target: { value: text },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));
  });
}

describe('CopilotPage', () => {
  it('keeps the disabled standing visible before any question is asked', () => {
    renderCopilot();

    expect(screen.getByTestId('copilot-page')).toBeInTheDocument();
    expect(screen.getByText('Operations Copilot')).toBeInTheDocument();
    expect(screen.getByText('Not active')).toBeInTheDocument();
    expect(screen.getByText(TEST_SESSION.planningSessionId)).toBeInTheDocument();
    expect(screen.getByText(TEST_SESSION.conversationId)).toBeInTheDocument();
    expect(
      screen.getByText(/does not activate production AI/i),
    ).toBeInTheDocument();
  });

  it('records a session-aware request and shows the unavailable closed sentence', async () => {
    const query = vi.fn(async (request) => unavailableResult(request));

    renderCopilot(createInjectedCopilotQueryClient(query));
    await ask('What is the current state of UAL9?');

    await waitFor(() => {
      expect(
        screen.getByText(MASTER_RESPONSE_STATUS_TEXT.UNAVAILABLE),
      ).toBeInTheDocument();
    });

    expect(query).toHaveBeenCalledTimes(1);
    const request = query.mock.calls[0]![0];
    expect(request.planningSessionId).toBe(TEST_SESSION.planningSessionId);
    expect(request.conversationId).toBe(TEST_SESSION.conversationId);
    expect(request.userText).toBe('What is the current state of UAL9?');
    expect(request.applicationRequestTimeUtc).toBe(TEST_APPLICATION_TIME);
    expect(request.priorTurns).toEqual([]);
    expect(screen.getByText(/not a verified-zero claim/i)).toBeInTheDocument();
  });

  it('displays unsupported results without inventing a diversion instruction', async () => {
    renderCopilot(
      createInjectedCopilotQueryClient(async (request) =>
        unsupportedResult(request),
      ),
    );
    await ask('Tell UAL9 to divert');

    await waitFor(() => {
      expect(
        screen.getByText(MASTER_RESPONSE_STATUS_TEXT.UNSUPPORTED),
      ).toBeInTheDocument();
    });

    expect(screen.getByText('Unsupported')).toBeInTheDocument();
    expect(screen.queryByText(/divert to/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Claims admitted: no/)).toBeInTheDocument();
  });

  it('renders injected aircraft and hazard evidence as display-only', async () => {
    renderCopilot(
      createInjectedCopilotQueryClient(async (request) =>
        evidenceDisplayResult(request),
      ),
    );
    await ask('Show UAL9 and the nearby SIGMET');

    await waitFor(() => {
      expect(screen.getByTestId('copilot-evidence')).toBeInTheDocument();
    });

    expect(screen.getAllByText('UAL9').length).toBeGreaterThan(0);
    expect(screen.getAllByText('sigmet-a').length).toBeGreaterThan(0);
    expect(screen.getByText('Insufficient evidence')).toBeInTheDocument();
    expect(
      screen.getByText(/Source authenticity: not established/),
    ).toBeInTheDocument();
    expect(screen.getByText('Not active')).toBeInTheDocument();
    expect(
      screen.getByText(/fixture data, not a live AWS read/i),
    ).toBeInTheDocument();
  });

  it('includes untrusted selector hints from the URL on the request', async () => {
    const query = vi.fn(async (request) => unavailableResult(request));

    renderCopilot(
      createInjectedCopilotQueryClient(query),
      '/copilot?aircraftId=aa0001&callsign=UAL9',
    );

    expect(
      screen.getByText(/Selector hints from the URL are untrusted text/i),
    ).toBeInTheDocument();

    await ask('Current state');

    await waitFor(() => expect(query).toHaveBeenCalled());
    expect(query.mock.calls[0]![0].selectorHints).toEqual({
      aircraftId: 'aa0001',
      callsign: 'UAL9',
      hazardId: undefined,
      airportId: undefined,
    });
  });

  it('carries prior turns into the next request', async () => {
    const query = vi.fn(async (request) => unavailableResult(request));

    renderCopilot(createInjectedCopilotQueryClient(query));
    await ask('First question');
    await waitFor(() => expect(query).toHaveBeenCalledTimes(1));

    await ask('Second question');
    await waitFor(() => expect(query).toHaveBeenCalledTimes(2));

    expect(query.mock.calls[1]![0].priorTurns).toEqual([
      { role: 'user', text: 'First question' },
      {
        role: 'assistant',
        text: MASTER_RESPONSE_STATUS_TEXT.UNAVAILABLE,
      },
    ]);
  });

  it('shows a retryable error without claiming the Copilot answered', async () => {
    renderCopilot(
      createInjectedCopilotQueryClient(async () => {
        throw new ApiError('upstream failed', { kind: 'server', status: 503 });
      }),
    );
    await ask('Current UAL9');

    await waitFor(() => {
      expect(screen.getByText(/Cannot load the Copilot response/)).toBeInTheDocument();
    });

    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(
      screen.queryByText(MASTER_RESPONSE_STATUS_TEXT.UNAVAILABLE),
    ).not.toBeInTheDocument();
  });

  it('uses the local disabled client without POSTing /ai/query', async () => {
    const fetchImpl = vi.fn();
    vi.stubGlobal('fetch', fetchImpl);

    renderWithProviders(
      <MemoryRouter initialEntries={['/copilot']}>
        <CopilotPage
          createSession={() => TEST_SESSION}
          createRequestId={() => 'disabled1'}
          now={() => new Date(TEST_APPLICATION_TIME)}
        />
      </MemoryRouter>,
    );

    await ask('Is production AI active?');

    await waitFor(() => {
      expect(
        screen.getByText(MASTER_RESPONSE_STATUS_TEXT.UNAVAILABLE),
      ).toBeInTheDocument();
    });

    expect(fetchImpl).not.toHaveBeenCalled();
    expect(
      screen.getByText(/This console does not call that route/i),
    ).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it('keeps a completed local preview readable and unadmitted', async () => {
    const client: CopilotQueryClient = {
      standing: LOCAL_DEV_PREVIEW_STANDING,
      query: async (request) =>
        assertCopilotQueryResult(
          localPreviewResult(
            request,
            {
              toolResults: [developerTool(), encounterTool()],
            },
            {
              evidence: [
                {
                  ...AIRCRAFT_EVIDENCE,
                  entityId: '407d6c',
                  aircraftId: '407d6c',
                  callsign: 'UAL407',
                  toolName: 'get_aircraft_operational_context',
                  toolCallId: 'tool-aircraft-1',
                  observationTimeUtc: '2026-11-07T17:48:18Z',
                  evaluationTimeUtc: '2026-11-07T18:00:05Z',
                  sourceName: 'wilvor-dev-aircraft-current-state',
                },
              ],
            },
          ),
        ),
    };

    renderCopilot(client);
    expect(screen.getByText(/Loopback developer preview/i)).toBeInTheDocument();

    await ask('What is stored for aircraft 407d6c?');

    await waitFor(() => {
      expect(screen.getByText(/Stored aircraft 407d6c/)).toBeInTheDocument();
    });

    expect(
      screen.getByText(MASTER_RESPONSE_STATUS_TEXT.INSUFFICIENT_EVIDENCE),
    ).toBeInTheDocument();
    expect(screen.getAllByText('37.62').length).toBeGreaterThan(0);
    expect(screen.getByText(/11m 42s before the request/)).toBeInTheDocument();
    expect(screen.getByText(/Stored freshness label: FRESH/)).toBeInTheDocument();
    expect(screen.getByText(/0 returned candidate rows/)).toBeInTheDocument();
    expect(
      screen.getByText(/does not mean the aircraft is free from hazards/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Source authenticity: not established/),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/Claims admitted: no/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Production authority: DENIED/)).toBeInTheDocument();
    const details = screen
      .getByText('Specialist verification and technical details')
      .closest('details');
    expect(details).not.toHaveAttribute('open');
    expect(details).toHaveTextContent('DIAGNOSTIC_NARRATIVE_ONLY');
    expect(screen.queryByText(/The aircraft is free from hazards/)).not.toBeInTheDocument();
  });

  it('does not submit blank operator text', async () => {
    const query = vi.fn(async (request) => unavailableResult(request));

    renderCopilot(createInjectedCopilotQueryClient(query));
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }));

    expect(query).not.toHaveBeenCalled();
  });
});
