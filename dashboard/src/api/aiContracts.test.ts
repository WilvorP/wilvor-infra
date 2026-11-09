import { describe, expect, it } from 'vitest';

import {
  CopilotContractError,
  createRouteDisabledResult,
  MASTER_RESPONSE_STATUS_TEXT,
  REQUIRED_LIMITATION_CODES,
  assertCopilotQueryRequest,
  assertCopilotQueryResult,
} from './aiContracts';
import {
  emptyEvidenceResult,
  evidenceDisplayResult,
  testRequest,
  unavailableResult,
  unsupportedResult,
} from '@/features/ai/fixtures';

describe('Copilot session-query contract', () => {
  it('accepts a well-formed request whose identities are pairwise distinct', () => {
    expect(assertCopilotQueryRequest(testRequest())).toMatchObject({
      userText: 'What is the current state of UAL9?',
    });
  });

  it('rejects colliding session identities', () => {
    expect(() =>
      assertCopilotQueryRequest(
        testRequest({
          masterRequestId: 'same-id',
          correlationId: 'same-id',
        }),
      ),
    ).toThrow(CopilotContractError);
  });

  it('rejects empty or padded operator text', () => {
    expect(() =>
      assertCopilotQueryRequest(testRequest({ userText: '' })),
    ).toThrow(/invalid_user_text/);
    expect(() =>
      assertCopilotQueryRequest(testRequest({ userText: '  padded  ' })),
    ).toThrow(/invalid_user_text/);
  });

  it('caps prior turns at the Live Ops public bound', () => {
    expect(() =>
      assertCopilotQueryRequest(
        testRequest({
          priorTurns: [
            { role: 'user', text: 'one' },
            { role: 'assistant', text: 'two' },
            { role: 'user', text: 'three' },
            { role: 'assistant', text: 'four' },
            { role: 'user', text: 'five' },
          ],
        }),
      ),
    ).toThrow(/too_many_prior_turns/);
  });

  it('keeps the disabled-route result unavailable and nonfactual', () => {
    const result = createRouteDisabledResult(testRequest());

    expect(result.state).toBe('UNAVAILABLE');
    expect(result.statusText).toBe(MASTER_RESPONSE_STATUS_TEXT.UNAVAILABLE);
    expect(result.assistantText).toBe(result.statusText);
    expect(result.claimsAdmitted).toBe(false);
    expect(result.productionAiActive).toBe(false);
    expect(result.standing.routeEnabled).toBe(false);
    expect(result.standing.routeKey).toBe('POST /ai/query');
    expect(result.pageEmpty).toBe(true);
    expect(result.verifiedZeroMinted).toBe(false);
    expect(result.evidence).toEqual([]);
    expect(result.limitationCodes).toEqual([...REQUIRED_LIMITATION_CODES]);
  });

  it('does not treat empty evidence as a verified zero', () => {
    const result = assertCopilotQueryResult(emptyEvidenceResult());

    expect(result.pageEmpty).toBe(true);
    expect(result.verifiedZeroMinted).toBe(false);
    expect(result.evidence).toHaveLength(0);
  });

  it('rejects an assistant sentence that is not the closed status text', () => {
    expect(() =>
      assertCopilotQueryResult({
        ...unsupportedResult(),
        assistantText: 'Divert UAL9 to KSFO.',
      }),
    ).toThrow(/assistant_text_must_match_status_text/);
  });

  it('rejects a result that claims production AI is active', () => {
    expect(() =>
      assertCopilotQueryResult({
        ...unavailableResult(),
        productionAiActive: true as unknown as false,
      }),
    ).toThrow(/production_ai_must_remain_inactive/);
  });

  it('rejects enabling POST /ai/query from the Copilot client contract', () => {
    expect(() =>
      assertCopilotQueryResult({
        ...unavailableResult(),
        standing: {
          ...unavailableResult().standing,
          routeEnabled: true as unknown as false,
        },
      }),
    ).toThrow(/ai_query_route_must_remain_disabled/);
  });

  it('rejects merging observation and evaluation clocks', () => {
    const result = evidenceDisplayResult();
    const [aircraft] = result.evidence;

    expect(() =>
      assertCopilotQueryResult({
        ...result,
        evidence: [
          {
            ...aircraft!,
            observationTimeUtc: '2026-11-07T18:00:00Z',
            evaluationTimeUtc: '2026-11-07T18:00:00Z',
          },
        ],
      }),
    ).toThrow(/clocks_must_remain_distinct/);
  });

  it('keeps injected evidence display-only: claims stay closed', () => {
    const result = assertCopilotQueryResult(evidenceDisplayResult());

    expect(result.claimsAdmitted).toBe(false);
    expect(result.sourceAuthenticity).toBe('NOT_ESTABLISHED');
    expect(result.productionAuthority).toBe('DENIED');
    expect(result.evidence.map((item) => item.entityKind)).toEqual([
      'AIRCRAFT',
      'HAZARD',
      'ENCOUNTER',
    ]);
    expect(result.evidence.every((item) => item.verifiedZeroMinted === false)).toBe(
      true,
    );
  });
});
