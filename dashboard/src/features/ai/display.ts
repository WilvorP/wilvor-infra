import type { StatusPresentation } from '@/utils/status';
import { humaniseToken } from '@/utils/format';

import type {
  CopilotEvidenceItem,
  CopilotLimitationCode,
  MasterResponsePolicyState,
} from '@/api/aiContracts';

export function presentSourceAuthenticity(): StatusPresentation {
  return { tone: 'unknown', label: 'Not established', glyph: '×' };
}

export function presentCopilotState(
  state: MasterResponsePolicyState,
): StatusPresentation {
  switch (state) {
    case 'UNSUPPORTED':
      return { tone: 'medium', label: 'Unsupported', glyph: '◆' };
    case 'UNAVAILABLE':
      return { tone: 'unknown', label: 'Unavailable', glyph: '×' };
    case 'INSUFFICIENT_EVIDENCE':
      return { tone: 'medium', label: 'Insufficient evidence', glyph: '◐' };
    case 'NOT_EVALUATED':
      return { tone: 'unknown', label: 'Not evaluated', glyph: '?' };
  }
}

export function presentLimitationCode(code: CopilotLimitationCode | string): string {
  return humaniseToken(code);
}

export function emptyEvidenceCaption(pageEmpty: boolean): string | null {
  if (!pageEmpty) {
    return null;
  }

  return 'No evidence rows were returned. This is not a verified-zero claim, and it is not an impacted-aircraft count.';
}

export function evidenceIdentity(item: CopilotEvidenceItem): string {
  if (item.entityKind === 'AIRCRAFT') {
    return item.callsign?.trim() || item.entityId;
  }

  return item.entityId;
}

export function evidenceInvestigationPath(
  item: CopilotEvidenceItem,
): string | null {
  if (item.entityKind === 'AIRCRAFT') {
    return `/aircraft/${encodeURIComponent(item.entityId)}`;
  }

  if (item.entityKind === 'HAZARD') {
    return null;
  }

  if (item.aircraftId) {
    return `/aircraft/${encodeURIComponent(item.aircraftId)}`;
  }

  return null;
}
