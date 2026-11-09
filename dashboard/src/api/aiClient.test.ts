import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  copilotClientPostsAiQuery,
  createDisabledCopilotQueryClient,
  createHostContractCopilotQueryClient,
  createInjectedCopilotQueryClient,
  createLocalDevCopilotQueryClient,
} from './aiClient';
import {
  CopilotContractError,
  LOCAL_DEV_COPILOT_PATH,
  MASTER_RESPONSE_STATUS_TEXT,
} from './aiContracts';
import {
  evidenceDisplayResult,
  testRequest,
  unavailableResult,
  unsupportedResult,
} from '@/features/ai/fixtures';

describe('Copilot query client', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('does not expose a live POST /ai/query transport', () => {
    expect(copilotClientPostsAiQuery()).toBe(false);
  });

  it('never calls fetch from the disabled client', async () => {
    const fetchImpl = vi.fn();
    vi.stubGlobal('fetch', fetchImpl);

    const client = createDisabledCopilotQueryClient();
    const result = await client.query(testRequest());

    expect(fetchImpl).not.toHaveBeenCalled();
    expect(result.standing.transport).toBe('LOCAL_DISABLED');
    expect(result.standing.routeEnabled).toBe(false);
    expect(result.productionAiActive).toBe(false);
    expect(result.state).toBe('UNAVAILABLE');
    expect(result.statusText).toBe(MASTER_RESPONSE_STATUS_TEXT.UNAVAILABLE);
    expect(result.pageEmpty).toBe(true);
    expect(result.verifiedZeroMinted).toBe(false);
  });

  it('returns injected fixture data without claiming the route is live', async () => {
    const fetchImpl = vi.fn();
    vi.stubGlobal('fetch', fetchImpl);

    const request = testRequest();
    const client = createInjectedCopilotQueryClient(async (received) =>
      unsupportedResult(received),
    );
    const result = await client.query(request);

    expect(fetchImpl).not.toHaveBeenCalled();
    expect(result.standing.transport).toBe('INJECTED_TEST');
    expect(result.standing.routeEnabled).toBe(false);
    expect(result.productionAiActive).toBe(false);
    expect(result.state).toBe('UNSUPPORTED');
    expect(result.assistantText).toBe(MASTER_RESPONSE_STATUS_TEXT.UNSUPPORTED);
  });

  it('still refuses injected results that admit operational claims', async () => {
    const client = createInjectedCopilotQueryClient(async (received) => ({
      ...evidenceDisplayResult(received),
      claimsAdmitted: true as unknown as false,
    }));

    await expect(client.query(testRequest())).rejects.toBeInstanceOf(
      CopilotContractError,
    );
  });

  it('host-contract client never fetches and keeps the route disabled', async () => {
    const fetchImpl = vi.fn();
    vi.stubGlobal('fetch', fetchImpl);

    const client = createHostContractCopilotQueryClient(async (received) =>
      unsupportedResult(received),
    );
    const result = await client.query(testRequest());

    expect(fetchImpl).not.toHaveBeenCalled();
    expect(result.standing.transport).toBe('HOST_CONTRACT');
    expect(result.standing.routeEnabled).toBe(false);
    expect(result.productionAiActive).toBe(false);
  });

  it('posts the loopback preview path and keeps production claims closed', async () => {
    const request = testRequest();
    const fetchImpl = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        ...unavailableResult(request),
        developerPreview: {
          notProduction: true,
          evidenceProvenance: 'OPERATOR_AUTHENTICATED_DEV',
          sourceAuthenticity: 'NOT_ESTABLISHED',
          claimsAdmitted: false,
          notice: 'NOT PRODUCTION. Loopback developer preview.',
          answer: 'GetItem returned no item. The record is missing.',
          specialistStatusText: null,
          routeStatus: 'AUTHORIZED_PLAN',
          specialists: ['LIVE_OPS'],
          blockedSpecialists: [],
          toolResults: [],
          awsOperations: [
            {
              operation: 'GetItem',
              table: 'wilvor-dev-aircraft-current-state',
              keyAttributes: ['aircraft_id'],
              bounded: true,
              provenance: 'OPERATOR_AUTHENTICATED_DEV',
            },
          ],
          modelOperations: [],
          principalLabel: null,
          missingEvidence: ['adb65b'],
          staleEvidence: [],
          partialEvidence: [],
          toolErrors: [],
          modelErrors: [],
        },
      }),
    }));
    vi.stubGlobal('fetch', fetchImpl);

    const result = await createLocalDevCopilotQueryClient().query(request);

    expect(fetchImpl).toHaveBeenCalledWith(
      LOCAL_DEV_COPILOT_PATH,
      expect.objectContaining({ method: 'POST' }),
    );
    expect(LOCAL_DEV_COPILOT_PATH).not.toContain('/ai/query');
    expect(result.standing.transport).toBe('LOCAL_DEV_PREVIEW');
    expect(result.standing.routeEnabled).toBe(false);
    expect(result.claimsAdmitted).toBe(false);
    expect(result.sourceAuthenticity).toBe('NOT_ESTABLISHED');
    expect(result.developerPreview?.answer).toContain('missing');
  });

  it('honours caller abort before invoking an injected handler', async () => {
    const handler = vi.fn();
    const client = createInjectedCopilotQueryClient(handler);
    const controller = new AbortController();
    controller.abort();

    await expect(
      client.query(testRequest(), { signal: controller.signal }),
    ).rejects.toMatchObject({ name: 'AbortError' });
    expect(handler).not.toHaveBeenCalled();
  });
});
