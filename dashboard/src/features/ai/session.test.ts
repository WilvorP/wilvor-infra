import { describe, expect, it } from 'vitest';

import { PRIOR_TURN_MAX } from '@/api/aiContracts';

import {
  buildCopilotQueryRequest,
  createCopilotRequestIdentity,
  createCopilotSessionIdentity,
  priorTurnsFromHistory,
  selectorHintsFromSearch,
  toUtcTimestamp,
} from './session';
import { TEST_SESSION } from './fixtures';

describe('Copilot session helpers', () => {
  it('mints pairwise-distinct session and request identities', () => {
    let n = 0;
    const randomId = () => `id${n++}`;
    const session = createCopilotSessionIdentity(randomId);
    const request = createCopilotRequestIdentity(session, randomId);
    const ids = [
      session.planningSessionId,
      session.conversationId,
      request.masterRequestId,
      request.correlationId,
    ];

    expect(new Set(ids).size).toBe(4);
    expect(session.planningSessionId.startsWith('plan-')).toBe(true);
    expect(session.conversationId.startsWith('conv-')).toBe(true);
  });

  it('keeps only the last Live Ops prior-turn window', () => {
    const prior = priorTurnsFromHistory([
      { role: 'user', text: 'one' },
      { role: 'assistant', text: 'two' },
      { role: 'user', text: 'three' },
      { role: 'assistant', text: 'four' },
      { role: 'user', text: 'five' },
    ]);

    expect(prior).toHaveLength(PRIOR_TURN_MAX);
    expect(prior[0]?.text).toBe('two');
    expect(prior[3]?.text).toBe('five');
  });

  it('reads untrusted selector hints from the URL without authorizing them', () => {
    const hints = selectorHintsFromSearch(
      new URLSearchParams('aircraftId=aa0001&callsign=UAL9'),
    );

    expect(hints).toEqual({
      aircraftId: 'aa0001',
      callsign: 'UAL9',
      hazardId: undefined,
      airportId: undefined,
    });
    expect(selectorHintsFromSearch(new URLSearchParams())).toBeNull();
  });

  it('builds a session-aware request from conversation history', () => {
    const request = buildCopilotQueryRequest({
      session: TEST_SESSION,
      request: {
        masterRequestId: 'mreq-a',
        correlationId: 'corr-a',
      },
      userText: '  current UAL9  ',
      priorTurns: [{ role: 'user', text: 'hello' }],
      applicationRequestTimeUtc: toUtcTimestamp(new Date('2026-11-07T18:00:00Z')),
      selectorHints: { aircraftId: 'aa0001' },
    });

    expect(request.userText).toBe('current UAL9');
    expect(request.planningSessionId).toBe(TEST_SESSION.planningSessionId);
    expect(request.conversationId).toBe(TEST_SESSION.conversationId);
    expect(request.priorTurns).toEqual([{ role: 'user', text: 'hello' }]);
    expect(request.selectorHints).toEqual({ aircraftId: 'aa0001' });
  });
});
