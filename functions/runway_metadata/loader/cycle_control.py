from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CycleDecisionType(str, Enum):
    FORCE_RELOAD = "FORCE_RELOAD"
    FIRST_LOAD = "FIRST_LOAD"
    NEW_CYCLE = "NEW_CYCLE"
    CORRECTED_PACKAGE = "CORRECTED_PACKAGE"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True)
class CycleDecision:
    decision: CycleDecisionType
    should_process: bool
    reason: str


def decide_cycle(
    *,
    current_cycle: str | None,
    current_source_hash: str | None,
    incoming_cycle: str,
    incoming_source_hash: str,
    force_reload: bool = False,
) -> CycleDecision:
    if force_reload:
        return CycleDecision(
            CycleDecisionType.FORCE_RELOAD,
            True,
            "force_reload was requested",
        )
    if not current_cycle or not current_source_hash:
        return CycleDecision(CycleDecisionType.FIRST_LOAD, True, "no successful cycle is recorded")
    if incoming_cycle != current_cycle:
        return CycleDecision(
            CycleDecisionType.NEW_CYCLE,
            True,
            f"incoming cycle {incoming_cycle} differs from current cycle {current_cycle}",
        )
    if incoming_source_hash != current_source_hash:
        return CycleDecision(
            CycleDecisionType.CORRECTED_PACKAGE,
            True,
            "the FAA package changed within the same source cycle",
        )
    return CycleDecision(
        CycleDecisionType.DUPLICATE,
        False,
        "the source cycle and package hash were already loaded",
    )
