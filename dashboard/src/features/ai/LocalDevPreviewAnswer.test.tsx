import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  assertCopilotQueryResult,
  MASTER_RESPONSE_STATUS_TEXT,
} from '@/api/aiContracts';

import { LocalDevPreviewAnswer, describeObservationAge } from './LocalDevPreviewAnswer';
import {
  developerPreview,
  developerTool,
  encounterTool,
  evidenceDisplayResult,
  localPreviewResult,
  testRequest,
} from './fixtures';

const STOPPED_BEFORE_VERIFICATION =
  'Live operations stopped before verification. No operational facts are verified.';

function expectNoUnsupportedConclusion() {
  const text = document.body.textContent ?? '';
  expect(text).not.toMatch(/The aircraft is free from hazards/);
  expect(text).not.toMatch(/The aircraft is clear of hazards/);
  expect(text).not.toMatch(/safe to fly/i);
  expect(text).not.toMatch(/no hazards were found/i);
  expect(text).not.toMatch(/verified zero/i);
  expect(text).not.toMatch(/source freshness is established/i);
  expect(text).not.toMatch(/the observation is fresh/i);
  expect(text).not.toMatch(/complete coverage/i);
}

describe('describeObservationAge', () => {
  it('reports the stored position age against the request clock', () => {
    expect(
      describeObservationAge('2026-11-08T23:48:12Z', '2026-11-08T23:59:54Z'),
    ).toBe('Stored position time was 11m 42s before the request.');
  });

  it('does not invent an age when the timestamp is missing or unusable', () => {
    expect(describeObservationAge(null, '2026-11-08T23:59:54Z')).toBeNull();
    expect(describeObservationAge('not-a-timestamp', '2026-11-08T23:59:54Z')).toBeNull();
  });

  it('flags a position timestamp later than the request clock', () => {
    expect(
      describeObservationAge('2026-11-08T23:59:54Z', '2026-11-08T23:48:12Z'),
    ).toMatch(/after the request clock/);
  });
});

describe('LocalDevPreviewAnswer', () => {
  it('shows a completed local preview without admitting the observations', () => {
    const request = testRequest({
      applicationRequestTimeUtc: '2026-11-08T23:59:54Z',
    });
    const result = localPreviewResult(request, {
      toolResults: [
        developerTool({
          positionTimeUtc: '2026-11-08T23:48:12Z',
          callsign: null,
        }),
        encounterTool(),
      ],
    });

    const admitted = assertCopilotQueryResult(result);
    expect(admitted.assistantText).toBe(admitted.statusText);
    expect(admitted.assistantText).toBe(
      MASTER_RESPONSE_STATUS_TEXT.INSUFFICIENT_EVIDENCE,
    );
    expect(admitted.claimsAdmitted).toBe(false);
    expect(admitted.sourceAuthenticity).toBe('NOT_ESTABLISHED');
    expect(admitted.productionAuthority).toBe('DENIED');
    expect(admitted.productionAiActive).toBe(false);
    expect(admitted.standing.routeEnabled).toBe(false);
    expect(admitted.developerPreview?.answer).not.toBe(admitted.assistantText);
    expect(admitted.developerPreview?.specialistStatusText).not.toBe(
      admitted.assistantText,
    );

    render(<LocalDevPreviewAnswer result={result} />);

    expect(screen.getAllByText(/NOT PRODUCTION/).length).toBeGreaterThan(0);
    expect(screen.getByText(/UNADMITTED DEV EVIDENCE/)).toBeInTheDocument();
    expect(screen.getByText(/Stored aircraft 407d6c/)).toBeInTheDocument();
    expect(screen.getByText(/Callsign: not reported/)).toBeInTheDocument();
    expect(screen.getByText(/Position timestamp: 2026-11-08T23:48:12Z/)).toBeInTheDocument();
    expect(screen.getByTestId('copilot-observation-age')).toHaveTextContent(
      '11m 42s before the request',
    );
    expect(screen.getByText('Latitude')).toBeInTheDocument();
    expect(screen.getByText('35000')).toBeInTheDocument();
    expect(screen.getByTestId('copilot-stored-freshness')).toHaveTextContent(
      'Stored freshness label: FRESH',
    );
    expect(screen.getByText(/not an independent source-freshness check/)).toBeInTheDocument();
    expect(screen.getByText(/not the evaluation clock/)).toBeInTheDocument();
    expect(screen.getByText(/Master route authorization/)).toBeInTheDocument();
    expect(screen.getByText(/AUTHORIZED_PLAN/)).toBeInTheDocument();
    expect(screen.getByText(/LIVE_OPS_ANSWER/)).toBeInTheDocument();
    expect(screen.getByText(/Specialist execution/)).toBeInTheDocument();
    expect(screen.getByText(/DECISION not executed/)).toBeInTheDocument();
    expect(screen.getByText(/HISTORICAL_ANALYTICS not executed/)).toBeInTheDocument();
    expect(screen.getByText(/Production evidence admission remains closed/)).toBeInTheDocument();
    expect(screen.getByText(/Production authority: DENIED/)).toBeInTheDocument();
    expect(screen.getByText(/0 returned candidate rows/)).toBeInTheDocument();
    expect(
      screen.getByText(/does not mean the aircraft is free from hazards/),
    ).toBeInTheDocument();
    const details = screen
      .getByText('Specialist verification and technical details')
      .closest('details');
    expect(details).not.toHaveAttribute('open');
    expect(details).toHaveTextContent('DIAGNOSTIC_NARRATIVE_ONLY');
    expect(details).toHaveTextContent(
      'Deterministic renderer output for local verification only.',
    );
    expectNoUnsupportedConclusion();
  });

  it('labels a budget stop without turning stored rows into admitted claims', () => {
    const result = localPreviewResult(testRequest(), {
      specialistStatusText: STOPPED_BEFORE_VERIFICATION,
      modelErrors: ['BUDGET_EXHAUSTED'],
      toolResults: [developerTool()],
    });

    render(<LocalDevPreviewAnswer result={result} />);

    expect(
      screen.getByText(/Specialist execution stopped before verification/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Model diagnostics: BUDGET_EXHAUSTED/)).toBeInTheDocument();
    expect(screen.getByText(/UNADMITTED DEV EVIDENCE/)).toBeInTheDocument();
    expect(screen.getByText(/Claims admitted: no/)).toBeInTheDocument();
    expect(result.assistantText).toBe(result.statusText);
    expectNoUnsupportedConclusion();
  });

  it('does not infer status when the aircraft row is missing', () => {
    const result = localPreviewResult(testRequest(), {
      toolResults: [
        developerTool({
          recordState: 'MISSING',
          status: 'SUCCESS',
          callsign: null,
          positionTimeUtc: null,
          freshnessStatus: null,
          observations: [],
        }),
      ],
    });

    render(<LocalDevPreviewAnswer result={result} />);

    expect(
      screen.getByRole('region', { name: 'Stored aircraft observation' }),
    ).toHaveTextContent('missing record');
    expect(screen.getByText(/No stored aircraft row was returned/)).toBeInTheDocument();
    expect(screen.getByText(/not an inactivity finding/)).toBeInTheDocument();
    expect(screen.getByText(/Callsign: not reported/)).toBeInTheDocument();
    expect(screen.getByText(/Position timestamp: not reported/)).toBeInTheDocument();
    expect(screen.getByText(/Stored freshness label: not reported/)).toBeInTheDocument();
    expect(screen.queryByText('37.62')).not.toBeInTheDocument();
    expect(screen.queryByTestId('copilot-observation-age')).not.toBeInTheDocument();
    expectNoUnsupportedConclusion();
  });

  it('treats zero encounter rows as an unevaluated count', () => {
    const result = localPreviewResult(testRequest(), {
      toolResults: [encounterTool({ encounterCandidateCount: 0 })],
    });

    render(<LocalDevPreviewAnswer result={result} />);

    expect(screen.getByText(/0 returned candidate rows/)).toBeInTheDocument();
    expect(
      screen.getByText(/does not mean the aircraft is free from hazards/),
    ).toBeInTheDocument();
    expectNoUnsupportedConclusion();
  });

  it('does not calculate an age when the observation timestamp is absent', () => {
    const result = localPreviewResult(testRequest(), {
      toolResults: [
        developerTool({
          positionTimeUtc: null,
          observations: [
            { field: 'latitude', label: 'Latitude', value: '37.62' },
          ],
        }),
      ],
    });

    render(<LocalDevPreviewAnswer result={result} />);

    expect(screen.getByText(/Position timestamp: not reported/)).toBeInTheDocument();
    expect(screen.getByText(/Observation age is not calculated/)).toBeInTheDocument();
    expect(screen.queryByText(/before the request/)).not.toBeInTheDocument();
    expect(screen.getByText('37.62')).toBeInTheDocument();
  });

  it('shows a stale stored row without calling it current', () => {
    const result = localPreviewResult(testRequest(), {
      toolResults: [
        developerTool({
          recordState: 'STALE',
          freshnessStatus: 'STALE',
          positionTimeUtc: '2026-11-07T16:00:00Z',
        }),
      ],
    });

    render(<LocalDevPreviewAnswer result={result} />);

    expect(
      screen.getByRole('region', { name: 'Stored aircraft observation' }),
    ).toHaveTextContent('stale record');
    expect(screen.getByText(/not current operational status/)).toBeInTheDocument();
    expect(screen.getByText(/Stored freshness label: STALE/)).toBeInTheDocument();
    expect(screen.getByTestId('copilot-observation-age')).toBeInTheDocument();
    expect(screen.queryByText(/This is current operational status/)).not.toBeInTheDocument();
    expectNoUnsupportedConclusion();
  });

  it('hides developer observations for non-preview clients', () => {
    const injected = evidenceDisplayResult();
    const { rerender } = render(<LocalDevPreviewAnswer result={injected} />);
    expect(screen.queryByTestId('copilot-developer-preview')).not.toBeInTheDocument();

    rerender(
      <LocalDevPreviewAnswer
        result={{
          ...injected,
          developerPreview: developerPreview({
            toolResults: [developerTool()],
          }),
        }}
      />,
    );
    expect(screen.queryByTestId('copilot-developer-preview')).not.toBeInTheDocument();
  });
});
