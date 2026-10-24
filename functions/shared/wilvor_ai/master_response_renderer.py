"""Deterministic nonfactual rendering of a Master response-policy handoff.

The text is the handoff's fixed status sentence. Limitation codes are the
closed handoff codes in enum order. This module does not add aircraft,
hazard, risk, count, route, or instruction text, and it does not call a model.
"""

from __future__ import annotations

from dataclasses import dataclass

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_parallel_contracts import (
    MasterResponseLimitation,
    MasterResponsePolicyHandoff,
    MasterResponsePolicyState,
)

_STATUS_TEXT = {
    MasterResponsePolicyState.NOT_EVALUATED: "Response policy has not been evaluated.",
    MasterResponsePolicyState.UNSUPPORTED: (
        "The request is unsupported. No operational conclusion is available."
    ),
    MasterResponsePolicyState.UNAVAILABLE: (
        "Required evidence is unavailable. No count or zero is inferred."
    ),
    MasterResponsePolicyState.INSUFFICIENT_EVIDENCE: (
        "Supplied evidence is not sufficient for an operational claim."
    ),
}


@dataclass(frozen=True)
class MasterResponseRender:
    """Fixed status text and ordered limitation codes. Not an operational answer."""

    state: MasterResponsePolicyState
    status_text: str
    limitation_codes: tuple[str, ...]
    claims_admitted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.state, MasterResponsePolicyState):
            raise ContractValidationError("invalid_response_policy_state")
        if self.status_text != _STATUS_TEXT[self.state]:
            raise ContractValidationError("invalid_status_text")
        if self.claims_admitted is not False:
            raise ContractValidationError("operational_claim_not_admitted")
        if not isinstance(self.limitation_codes, tuple) or any(
            not isinstance(item, str) for item in self.limitation_codes
        ):
            raise ContractValidationError("invalid_limitation_codes")
        allowed = {item.value for item in MasterResponseLimitation}
        if any(item not in allowed for item in self.limitation_codes):
            raise ContractValidationError("invalid_limitation_codes")
        if self.limitation_codes != tuple(sorted(self.limitation_codes, key=_limitation_order)):
            raise ContractValidationError("limitation_order_not_deterministic")


def render_master_response(handoff: MasterResponsePolicyHandoff) -> MasterResponseRender:
    """Copy the fixed handoff sentence. Free-form instruction text is not an input."""

    if not isinstance(handoff, MasterResponsePolicyHandoff):
        raise ContractValidationError("invalid_response_policy")
    codes = tuple(
        item.value for item in MasterResponseLimitation if item in handoff.limitations
    )
    return MasterResponseRender(
        state=handoff.state,
        status_text=handoff.status_text,
        limitation_codes=codes,
    )


def _limitation_order(value: str) -> int:
    ordering = {item.value: index for index, item in enumerate(MasterResponseLimitation)}
    return ordering[value]
