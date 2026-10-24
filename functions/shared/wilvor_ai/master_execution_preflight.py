"""Offline execution-preflight boundary for a later integration review.

The future sequence is:

1. MA2 authorized planning
2. Separately reviewed execution authorization
3. Trusted adapter invocation
4. Native specialist result
5. Provenance validation
6. MA4 checks
7. MA5 response policy

This module stops before step 2. ``execution_preflight_for`` still sees MA2
execution authority ``DENIED``, so a valid route is not permission to call a
specialist. A planned invocation is not a completed execution. Supplied or
synthetic evidence is not an authenticated production read. A public Live Ops
reference is not a verified tool result.

``CONTRACT_READY_FOR_FUTURE_INTEGRATION`` can be named on a report. Naming it
does not dispatch, does not satisfy a provenance requirement, and does not
grant production authority.

MA4 admission and MA4 corroboration are not called here. Neither one replaces
source authentication.

Missing before any production read can be treated as authentic:

- A reviewed execution authority other than ``DENIED``
- Trusted source-adapter identity
- An authorized execution context
- Source and read provenance
- Request and entity binding proved by the executing adapter
- Specialist-specific observation and evaluation times, without inferring
  that one specialist's timestamp equals another's
- Source-version integrity where a version is claimed
- Completeness and geographic scope for a population-wide claim
- A Decision target authorized separately from user text, a callsign, or an
  expected aircraft id
- A Historical run supplied by that specialist, never fabricated here
- Dashboard integration that keeps the Operational API Lambda off this path

This module is not an application entrypoint. It does not construct a cloud
session, a model provider, or a specialist runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_contracts import MasterProductionAuthority
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_parallel_contracts import (
    MasterExecutionPreflightReport,
    MasterExecutionPreflightState,
    MasterParallelContractBundle,
    MasterProvenanceRequirement,
    execution_preflight_for,
)
from wilvor_ai.master_routing import MasterRouteExecutionAuthority


_EVALUATED_STATES = (
    MasterExecutionPreflightState.NOT_EXECUTABLE,
    MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION,
)


def _reject_forged_authority(
    *,
    source_authenticity: object,
    production_authority: object,
    execution_authority: object,
    executable: object,
) -> None:
    if source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
        raise ContractValidationError("source_authenticity_not_established")
    if production_authority is not MasterProductionAuthority.DENIED:
        raise ContractValidationError("production_authority_not_issued")
    if execution_authority is not MasterRouteExecutionAuthority.DENIED:
        raise ContractValidationError("execution_authority_not_granted")
    if executable is not False:
        raise ContractValidationError("execution_not_granted")


@dataclass(frozen=True)
class MasterExecutionPreflightEvaluation:
    """One offline preflight. This object has no dispatch operation.

    ``stops_before_execution_authorization`` records that step 2 of the
    integration sequence has not been reviewed. ``dispatch_permitted`` cannot
    be true under the current contracts, including when the report state is
    ``CONTRACT_READY_FOR_FUTURE_INTEGRATION``.
    """

    bundle: MasterParallelContractBundle
    report: MasterExecutionPreflightReport
    dispatch_permitted: bool = False
    stops_before_execution_authorization: bool = True

    def __post_init__(self) -> None:
        if self.dispatch_permitted is not False:
            raise ContractValidationError("execution_not_granted")
        if self.stops_before_execution_authorization is not True:
            raise ContractValidationError("execution_not_granted")
        if not isinstance(self.bundle, MasterParallelContractBundle):
            raise ContractValidationError("invalid_parallel_contract")
        if not isinstance(self.report, MasterExecutionPreflightReport):
            raise ContractValidationError("invalid_execution_preflight")
        if self.report.workflow is not self.bundle:
            raise ContractValidationError("master_request_mismatch")
        if self.report.executable is not False:
            raise ContractValidationError("execution_not_granted")
        if self.report.missing_provenance != tuple(MasterProvenanceRequirement):
            raise ContractValidationError("provenance_requirement_not_satisfied")
        _reject_forged_authority(
            source_authenticity=self.report.source_authenticity,
            production_authority=self.report.production_authority,
            execution_authority=self.report.execution_authority,
            executable=self.report.executable,
        )


def bind_preflight_report(
    bundle: MasterParallelContractBundle,
    report: MasterExecutionPreflightReport,
    *,
    dispatch_permitted: bool = False,
) -> MasterExecutionPreflightEvaluation:
    """Attach a report to its bundle without invoking a specialist.

    The current preflight helper never returns
    ``CONTRACT_READY_FOR_FUTURE_INTEGRATION``. A report that names that state
    still binds with dispatch closed and every provenance requirement missing.
    """

    if dispatch_permitted is not False:
        raise ContractValidationError("execution_not_granted")
    return MasterExecutionPreflightEvaluation(
        bundle=bundle,
        report=report,
        dispatch_permitted=False,
    )


def evaluate_execution_preflight(
    bundle: MasterParallelContractBundle,
    *,
    source_authenticity: object = MasterSourceAuthenticity.NOT_ESTABLISHED,
    production_authority: object = MasterProductionAuthority.DENIED,
    execution_authority: object = MasterRouteExecutionAuthority.DENIED,
    executable: object = False,
) -> MasterExecutionPreflightEvaluation:
    """Evaluate one bundle. Supplied evidence does not change the denial.

    An empty plan is ``NOT_EXECUTABLE``. Any planned step is
    ``BLOCKED_BY_AUTHORIZATION``. Provenance stays entirely missing. This
    function does not return ``CONTRACT_READY_FOR_FUTURE_INTEGRATION``.
    """

    _reject_forged_authority(
        source_authenticity=source_authenticity,
        production_authority=production_authority,
        execution_authority=execution_authority,
        executable=executable,
    )
    if not isinstance(bundle, MasterParallelContractBundle):
        raise ContractValidationError("invalid_parallel_contract")
    report = execution_preflight_for(bundle)
    if report.state not in _EVALUATED_STATES:
        raise ContractValidationError("execution_not_granted")
    return bind_preflight_report(bundle, report)
