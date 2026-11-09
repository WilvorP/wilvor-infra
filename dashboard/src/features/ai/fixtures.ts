import {
  COPILOT_SESSION_QUERY_SCHEMA,
  INJECTED_COPILOT_STANDING,
  LIVE_OPS_COMPLETENESS_NOTICE,
  LOCAL_DEV_PREVIEW_STANDING,
  MASTER_RESPONSE_STATUS_TEXT,
  OPERATOR_AUTHENTICATED_DEV,
  REQUIRED_LIMITATION_CODES,
  ROUTE_DISABLED_NOTICE,
  type CopilotDeveloperPreview,
  type CopilotDeveloperToolResult,
  type CopilotEvidenceItem,
  type CopilotLimitationCode,
  type CopilotQueryRequest,
  type CopilotQueryResult,
  type MasterResponsePolicyState,
} from '@/api/aiContracts';

export const TEST_SESSION = {
  planningSessionId: 'plan-testsession0001',
  conversationId: 'conv-testsession0001',
} as const;

export const TEST_REQUEST_IDS = {
  masterRequestId: 'mreq-testrequest0001',
  correlationId: 'corr-testrequest0001',
} as const;

export const TEST_APPLICATION_TIME = '2026-11-07T18:00:00Z';

export function testRequest(
  overrides: Partial<CopilotQueryRequest> = {},
): CopilotQueryRequest {
  return {
    schemaVersion: COPILOT_SESSION_QUERY_SCHEMA,
    liveOpsSchemaVersion: 'wilvor.ai.live_ops_public.v1',
    masterRequestId: TEST_REQUEST_IDS.masterRequestId,
    planningSessionId: TEST_SESSION.planningSessionId,
    conversationId: TEST_SESSION.conversationId,
    correlationId: TEST_REQUEST_IDS.correlationId,
    applicationRequestTimeUtc: TEST_APPLICATION_TIME,
    userText: 'What is the current state of UAL9?',
    priorTurns: [],
    selectorHints: null,
    ...overrides,
  };
}

function baseResult(
  request: CopilotQueryRequest,
  state: MasterResponsePolicyState,
  extraLimitations: readonly CopilotLimitationCode[] = [],
): Omit<CopilotQueryResult, 'evidence' | 'pageEmpty' | 'truncated' | 'hasMore'> {
  const limitationCodes = [
    ...REQUIRED_LIMITATION_CODES,
    ...extraLimitations,
  ].filter((code, index, all) => all.indexOf(code) === index);

  return {
    schemaVersion: COPILOT_SESSION_QUERY_SCHEMA,
    standing: INJECTED_COPILOT_STANDING,
    masterRequestId: request.masterRequestId,
    planningSessionId: request.planningSessionId,
    conversationId: request.conversationId,
    correlationId: request.correlationId,
    applicationRequestTimeUtc: request.applicationRequestTimeUtc,
    state,
    statusText: MASTER_RESPONSE_STATUS_TEXT[state],
    assistantText: MASTER_RESPONSE_STATUS_TEXT[state],
    limitationCodes,
    claimsAdmitted: false,
    productionAuthority: 'DENIED',
    sourceAuthenticity: 'NOT_ESTABLISHED',
    requestCoverage: 'NOT_ESTABLISHED',
    completenessNotice: LIVE_OPS_COMPLETENESS_NOTICE,
    routeDisabledNotice: ROUTE_DISABLED_NOTICE,
    productionAiActive: false,
    verifiedZeroMinted: false,
  };
}

export function unavailableResult(
  request: CopilotQueryRequest = testRequest(),
): CopilotQueryResult {
  return {
    ...baseResult(request, 'UNAVAILABLE'),
    evidence: [],
    pageEmpty: true,
    truncated: false,
    hasMore: false,
  };
}

export function unsupportedResult(
  request: CopilotQueryRequest = testRequest(),
): CopilotQueryResult {
  return {
    ...baseResult(request, 'UNSUPPORTED', [
      'REQUEST_COVERAGE_NOT_ESTABLISHED',
    ]),
    evidence: [],
    pageEmpty: true,
    truncated: false,
    hasMore: false,
  };
}

export function insufficientEvidenceResult(
  request: CopilotQueryRequest = testRequest(),
): CopilotQueryResult {
  return {
    ...baseResult(request, 'INSUFFICIENT_EVIDENCE', [
      'SUPPLIED_EVIDENCE_NOT_AUTHENTICATED',
    ]),
    evidence: [],
    pageEmpty: true,
    truncated: false,
    hasMore: false,
  };
}

export const AIRCRAFT_EVIDENCE: CopilotEvidenceItem = {
  entityKind: 'AIRCRAFT',
  entityId: 'aa0001',
  callsign: 'UAL9',
  aircraftId: 'aa0001',
  hazardId: null,
  encounterId: null,
  toolName: 'get_aircraft_current_state',
  toolCallId: 'tool-aircraft-1',
  sourceName: 'wilvor_aircraft_current_state',
  sourceVersion: 'v-aircraft-17',
  observationTimeUtc: '2026-11-07T17:59:40Z',
  evaluationTimeUtc: '2026-11-07T18:00:05Z',
  freshnessBand: 'FRESH',
  limitationCodes: ['SOURCE_AUTHENTICITY_NOT_ESTABLISHED'],
  truncated: false,
  hasMore: false,
  pageEmpty: false,
  verifiedZeroMinted: false,
};

export const HAZARD_EVIDENCE: CopilotEvidenceItem = {
  entityKind: 'HAZARD',
  entityId: 'sigmet-a',
  callsign: null,
  aircraftId: null,
  hazardId: 'sigmet-a',
  encounterId: null,
  toolName: 'get_hazard',
  toolCallId: 'tool-hazard-1',
  sourceName: 'wilvor_active_hazards',
  sourceVersion: 'v-hazard-4',
  observationTimeUtc: '2026-11-07T17:50:00Z',
  evaluationTimeUtc: '2026-11-07T18:00:05Z',
  freshnessBand: 'AVAILABLE',
  limitationCodes: [
    'SOURCE_AUTHENTICITY_NOT_ESTABLISHED',
    'SUPPLIED_EVIDENCE_NOT_AUTHENTICATED',
  ],
  truncated: false,
  hasMore: false,
  pageEmpty: false,
  verifiedZeroMinted: false,
};

export const ENCOUNTER_EVIDENCE: CopilotEvidenceItem = {
  entityKind: 'ENCOUNTER',
  entityId: 'enc-aa0001-sigmet-a',
  callsign: 'UAL9',
  aircraftId: 'aa0001',
  hazardId: 'sigmet-a',
  encounterId: 'enc-aa0001-sigmet-a',
  toolName: 'get_encounter',
  toolCallId: 'tool-encounter-1',
  sourceName: 'wilvor_active_encounters',
  sourceVersion: null,
  observationTimeUtc: '2026-11-07T17:58:12Z',
  evaluationTimeUtc: '2026-11-07T18:00:05Z',
  freshnessBand: null,
  limitationCodes: ['SOURCE_AUTHENTICITY_NOT_ESTABLISHED'],
  truncated: true,
  hasMore: true,
  pageEmpty: false,
  verifiedZeroMinted: false,
};

export function evidenceDisplayResult(
  request: CopilotQueryRequest = testRequest(),
): CopilotQueryResult {
  return {
    ...baseResult(request, 'INSUFFICIENT_EVIDENCE', [
      'SUPPLIED_EVIDENCE_NOT_AUTHENTICATED',
      'LIVE_OPS_REFS_NOT_TOOL_RESULTS',
    ]),
    evidence: [AIRCRAFT_EVIDENCE, HAZARD_EVIDENCE, ENCOUNTER_EVIDENCE],
    pageEmpty: false,
    truncated: true,
    hasMore: true,
  };
}

export function emptyEvidenceResult(
  request: CopilotQueryRequest = testRequest(),
): CopilotQueryResult {
  return {
    ...baseResult(request, 'UNAVAILABLE'),
    evidence: [],
    pageEmpty: true,
    truncated: false,
    hasMore: false,
  };
}

export function developerTool(
  overrides: Partial<CopilotDeveloperToolResult> = {},
): CopilotDeveloperToolResult {
  return {
    toolName: 'get_aircraft_operational_context',
    toolCallId: 'tool-aircraft-1',
    status: 'SUCCESS',
    recordState: 'PRESENT',
    aircraftId: '407d6c',
    callsign: 'UAL407',
    positionTimeUtc: '2026-11-07T17:48:18Z',
    freshnessStatus: 'FRESH',
    observations: [
      { field: 'latitude', label: 'Latitude', value: '37.62' },
      { field: 'longitude', label: 'Longitude', value: '-122.38' },
      { field: 'baro_altitude_ft', label: 'Baro altitude ft', value: '35000' },
    ],
    encounterCandidateCount: null,
    asOfUtc: '2026-11-07T18:00:05Z',
    sourceName: 'wilvor-dev-aircraft-current-state',
    limitations: ['SOURCE_AUTHENTICITY_NOT_ESTABLISHED'],
    ...overrides,
  };
}

export function encounterTool(
  overrides: Partial<CopilotDeveloperToolResult> = {},
): CopilotDeveloperToolResult {
  return developerTool({
    toolName: 'search_current_encounters',
    toolCallId: 'tool-encounter-1',
    recordState: 'UNKNOWN',
    callsign: null,
    positionTimeUtc: null,
    freshnessStatus: null,
    observations: [],
    encounterCandidateCount: 0,
    asOfUtc: null,
    sourceName: 'wilvor-dev-aircraft-hazard-encounter',
    ...overrides,
  });
}

export function developerPreview(
  overrides: Partial<CopilotDeveloperPreview> = {},
): CopilotDeveloperPreview {
  return {
    notProduction: true,
    evidenceProvenance: OPERATOR_AUTHENTICATED_DEV,
    sourceAuthenticity: 'NOT_ESTABLISHED',
    claimsAdmitted: false,
    notice:
      'NOT PRODUCTION. This is a loopback developer preview. POST /ai/query stays disabled. Claims are not admitted and source authenticity is not established.',
    answer: 'DIAGNOSTIC_NARRATIVE_ONLY. This narrative must not replace the Copilot answer.',
    specialistStatusText:
      'Deterministic renderer output for local verification only.',
    routeStatus: 'AUTHORIZED_PLAN',
    routeKinds: ['LIVE_OPS_ANSWER'],
    terminalDecision: 'PLAN_COMPLETE',
    specialists: ['LIVE_OPS'],
    blockedSpecialists: [
      {
        specialist: 'DECISION',
        reason: 'Decision specialist was not executed.',
      },
      {
        specialist: 'HISTORICAL_ANALYTICS',
        reason: 'Historical specialist was not executed. No Athena query was issued.',
      },
    ],
    toolResults: [],
    awsOperations: [],
    modelOperations: [],
    principalLabel: null,
    missingEvidence: [],
    staleEvidence: [],
    partialEvidence: [],
    toolErrors: [],
    modelErrors: [],
    ...overrides,
  };
}

export function localPreviewResult(
  request: CopilotQueryRequest = testRequest(),
  previewOverrides: Partial<CopilotDeveloperPreview> = {},
  options: {
    state?: MasterResponsePolicyState;
    evidence?: readonly CopilotEvidenceItem[];
  } = {},
): CopilotQueryResult {
  const evidence = options.evidence ?? [];
  const extraLimitations: CopilotLimitationCode[] =
    evidence.length > 0 ? ['SUPPLIED_EVIDENCE_NOT_AUTHENTICATED'] : [];

  return {
    ...baseResult(
      request,
      options.state ?? 'INSUFFICIENT_EVIDENCE',
      extraLimitations,
    ),
    standing: LOCAL_DEV_PREVIEW_STANDING,
    evidence,
    pageEmpty: evidence.length === 0,
    truncated: false,
    hasMore: false,
    developerPreview: developerPreview(previewOverrides),
  };
}
