"""Sprint 3D expected interfaces and execution tiers.

Agent F owns this module. It records the integration surface Sprint 3D must
add. It does not implement those hooks and it does not change Sprint 3C
modules. A passing offline classification is not a live AWS result and not
a production release.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping


SCHEMA_VERSION = "wilvor.ai.sprint3d.e2e_validation.v1"
BASELINE_CONTRACT = "wilvor.ai.sprint3c.shared_integration_contract.v1"
REAL_AWS_GATE_ENV = "WILVOR_SPRINT3D_REAL_AWS_NONPRODUCTION"
REAL_AWS_ACCOUNT_ENV = "WILVOR_SPRINT3D_AWS_ACCOUNT_ID"
REAL_AWS_REGION_ENV = "WILVOR_SPRINT3D_AWS_REGION"
REAL_AWS_LEDGER_TABLE_ENV = "WILVOR_SPRINT3D_LEDGER_TABLE"
REAL_AWS_WRITES_ENV = "WILVOR_SPRINT3D_AWS_WRITES"
REQUIRED_LEDGER_TABLE = "wilvor_master_execution_ledger"

SIMULATED_CLIENT_NAMES = frozenset(
    {
        "OfflineAwsReadClient",
        "FakeDynamoClient",
        "InMemoryMasterExecutionLedgerStore",
        "LocalHarnessConsumptionLedger",
    }
)

REPO_ROOT = Path(__file__).resolve().parents[2]
AI_QUERY_VARIABLES = REPO_ROOT / "modules" / "operational_api" / "variables.tf"


class ExecutionTier(str, Enum):
    """Three results that must not be collapsed into one pass."""

    OFFLINE_SIMULATED = "OFFLINE_SIMULATED"
    REAL_AWS_INTEGRATION = "REAL_AWS_INTEGRATION"
    PRODUCTION_RELEASE = "PRODUCTION_RELEASE"


class RealAwsGateState(str, Enum):
    CLOSED = "CLOSED"
    FAIL_CLOSED = "FAIL_CLOSED"
    READY_TO_PROBE = "READY_TO_PROBE"


@dataclass(frozen=True)
class ExecutionClassification:
    """How one run is allowed to be described."""

    tier: ExecutionTier
    live_pass: bool
    release_ready: bool
    reason: str

    def __post_init__(self) -> None:
        if self.tier is ExecutionTier.OFFLINE_SIMULATED and self.live_pass is not False:
            raise ValueError("simulated_execution_is_not_a_live_aws_pass")
        if self.tier is not ExecutionTier.PRODUCTION_RELEASE and self.release_ready is not False:
            raise ValueError("release_requires_cleared_acceptance_gates")
        if self.live_pass is True and self.tier is not ExecutionTier.REAL_AWS_INTEGRATION:
            raise ValueError("live_pass_requires_real_aws_tier")


@dataclass(frozen=True)
class ExpectedSprint3DInterface:
    """A hook later agents must supply. Absence is the current baseline."""

    interface_id: str
    module: str
    entrypoint: str
    baseline: str
    requirement: str


@dataclass(frozen=True)
class HookLocation:
    interface_id: str
    module: str
    entrypoint: str
    status: str
    requirement: str
    blocks_release: bool


@dataclass(frozen=True)
class AcceptanceGate:
    gate_id: str
    requirement: str
    cleared: bool
    blocker: str


@dataclass(frozen=True)
class AcceptanceReport:
    schema_version: str
    ready: bool
    tier: ExecutionTier
    gates: tuple[AcceptanceGate, ...]
    missing_hooks: tuple[HookLocation, ...]

    def blockers(self) -> tuple[str, ...]:
        return tuple(gate.blocker for gate in self.gates if gate.cleared is False)


@dataclass(frozen=True)
class RealAwsGateStatus:
    state: RealAwsGateState
    executed: bool
    live_pass: bool
    reason: str
    missing_hooks: tuple[str, ...]


EXPECTED_SPRINT3D_INTERFACES: tuple[ExpectedSprint3DInterface, ...] = (
    ExpectedSprint3DInterface(
        interface_id="I3D-1",
        module="wilvor_ai.sprint3d_authentic_invocation",
        entrypoint="verify_authentic_invocation",
        baseline="I1 assess_master_gateway_identity / Sprint3CIngressPrincipalPort.evaluate",
        requirement=(
            "Mint a principal whose record kind is REVIEWED_HOST_ADMISSION only after "
            "the twelve ISSUANCE_CONTROL_IDS are satisfied by an operator-owned manifest. "
            "The Lambda event, Authorization header, authorizer.jwt, Lambda context, and "
            "trusted flag stay observations."
        ),
    ),
    ExpectedSprint3DInterface(
        interface_id="I3D-2",
        module="wilvor_ai.sprint3d_production_grant",
        entrypoint="consume_production_grant",
        baseline="I2 LocalHarnessGrantIssuer and I3 Sprint3CDurableGrantLedger.consume_signed_grant",
        requirement=(
            "Sign and consume one grant bound to one principal, one session, one invocation, "
            "one policy revision, one resource scope, and one deadline. Replay, expiry, and "
            "cross-principal use fail closed. The signed nonce and the ledger nonce are one "
            "value. HMAC replay prevention stays false until a reviewed signing-key store exists."
        ),
    ),
    ExpectedSprint3DInterface(
        interface_id="I3D-3",
        module="wilvor_ai.sprint3d_verified_ledger",
        entrypoint="verify_ledger_account_and_table",
        baseline="I3 DynamoDbMasterExecutionLedgerStore and Sprint3CDurableGrantLedger",
        requirement=(
            "Prove the AWS account and the table wilvor_master_execution_ledger before a "
            "conditional TransactWriteItems is treated as a real ledger write. Competing "
            "writers share one fence. A crash after STARTED is an open attempt, not specialist "
            "success and not exactly-once execution."
        ),
    ),
    ExpectedSprint3DInterface(
        interface_id="I3D-4",
        module="wilvor_ai.sprint3d_production_scoped_read",
        entrypoint="execute_production_scoped_read",
        baseline="I5 Sprint3CScopedReadPort.execute",
        requirement=(
            "Allow only GET_ITEM and QUERY on server-allowlisted tables, resources, regions, "
            "and decision targets. Scan, SQL, writes, wildcards, and raw ARNs fail before a "
            "client call. Caller scope does not widen the server scope."
        ),
    ),
    ExpectedSprint3DInterface(
        interface_id="I3D-5",
        module="wilvor_ai.sprint3d_production_scoped_read",
        entrypoint="execute_bounded_read",
        baseline="I5 RuntimeBudgetSession, HostCancellation, and ScopedAwsReadPort pagination",
        requirement=(
            "Enforce server budget ceilings, the server deadline, and host cancellation. "
            "A repeated idempotency key calls the adapter once. Pagination reports has_more "
            "and truncation and does not follow ExclusiveStartKey."
        ),
    ),
    ExpectedSprint3DInterface(
        interface_id="I3D-6",
        module="wilvor_ai.sprint3d_source_evidence",
        entrypoint="evaluate_source_evidence",
        baseline="I6 integrate_host_read_evidence and sprint3c_evidence_response",
        requirement=(
            "TrustedAwsSourceReceipt is issued only by SourceEvidenceHost after an approved "
            "adapter runs for one consumed grant. evaluate_source_evidence admits a factual "
            "claim only when every prerequisite holds. A 3C local receipt is not that receipt. "
            "GLOBAL_EVIDENCE_ADMISSION_ENABLED stays false. Application, source, and evaluation "
            "clocks stay distinct. source_version and query_id absence stays absence."
        ),
    ),
    ExpectedSprint3DInterface(
        interface_id="I3D-7",
        module="wilvor_ai.sprint3d_source_evidence",
        entrypoint="trusted_receipt_interface",
        baseline="I7 admit_master_evidence geographic provenance checks",
        requirement=(
            "Aircraft identity comes from the authorized record. SIGMET intersection requires "
            "reviewed polygon geometry. SOURCE_LABEL_ESTABLISHES_GEOGRAPHY and "
            "REGIONAL_TEXT_ESTABLISHES_POLYGON_INTERSECTION stay false. A California string "
            "is not an impacted-aircraft count."
        ),
    ),
    ExpectedSprint3DInterface(
        interface_id="I3D-8",
        module="wilvor_ai.sprint3d_native_specialists",
        entrypoint="execute_historical_native",
        baseline="I4 Sprint3CNativeSpecialistOrchestrator",
        requirement=(
            "execute_historical_native calls HistoricalAnalyticsSpecialist.run only after "
            "reserve_native_dispatch and the current fence, using the host eligible window. "
            "execute_decision_native calls DecisionSpecialist.run only after source currentness, "
            "allowed population, and a separate recommendation permission. Offline execution "
            "stays NATIVE_OFFLINE_EXECUTION_IS_PRODUCTION_DISPATCH false and is not source evidence. "
            "Live Ops remains LiveOpsSpecialist.run with LiveOpsRunRequest and LiveOpsTrustedContext."
        ),
    ),
    ExpectedSprint3DInterface(
        interface_id="I3D-9",
        module="wilvor_ai.sprint3d_response_safety",
        entrypoint="render_authenticated_response",
        baseline="I8 evaluate_master_response_policy and render_master_response",
        requirement=(
            "Set claims_admitted only from an admission whose authenticity was established by "
            "I3D-6. MA5 text must not add verified-zero, low-risk-from-absence, impact counts, "
            "or safety or diversion instructions."
        ),
    ),
    ExpectedSprint3DInterface(
        interface_id="I3D-10",
        module="wilvor_ai.sprint3d_real_aws_probe",
        entrypoint="probe_nonproduction_readonly",
        baseline="Sprint 3C section 9 AWS nonproduction list, deferred past CP7",
        requirement=(
            "Read-only nonproduction checks: apigatewayv2:GetApi, lambda:GetPolicy, a ledger "
            "describe against wilvor_master_execution_ledger, and one GET_ITEM or QUERY on a "
            "reviewed specialist table. No writes. CloudTrail attributes the invoke. An injected "
            "fake client cannot satisfy this probe."
        ),
    ),
)


def classify_execution(
    *,
    client_type_name: str,
    live_aws_connection: bool,
    verified_account_provenance: bool,
    production_flags_open: bool,
    probe_executed: bool = False,
) -> ExecutionClassification:
    """Classify a run. Simulated clients and unverified AWS-shaped clients stay offline."""

    simulated = client_type_name in SIMULATED_CLIENT_NAMES or live_aws_connection is not True
    if simulated or probe_executed is not True or verified_account_provenance is not True:
        reason = "offline_simulated"
        if not simulated and (probe_executed is not True or verified_account_provenance is not True):
            reason = "aws_shaped_client_without_verified_probe"
        return ExecutionClassification(
            tier=ExecutionTier.OFFLINE_SIMULATED,
            live_pass=False,
            release_ready=False,
            reason=reason,
        )
    reason = "real_aws_probe_recorded"
    if production_flags_open is True:
        reason = "production_flags_are_not_acceptance"
    return ExecutionClassification(
        tier=ExecutionTier.REAL_AWS_INTEGRATION,
        live_pass=True,
        release_ready=False,
        reason=reason,
    )


def refuse_unverified_live_pass(classification: ExecutionClassification) -> None:
    """Stop a caller from recording a simulated or unverified run as live AWS."""

    from wilvor_ai.contracts import ContractValidationError

    if classification.live_pass is not True or classification.tier is not ExecutionTier.REAL_AWS_INTEGRATION:
        raise ContractValidationError("simulated_execution_is_not_a_live_aws_pass")


def locate_sprint3d_hooks() -> tuple[HookLocation, ...]:
    """Resolve expected modules. Present-but-unverified still blocks release."""

    found: list[HookLocation] = []
    for item in EXPECTED_SPRINT3D_INTERFACES:
        spec = importlib.util.find_spec(item.module)
        if spec is None:
            status = "ABSENT"
        else:
            module = importlib.import_module(item.module)
            status = "PRESENT_UNVERIFIED" if callable(getattr(module, item.entrypoint, None)) else "ABSENT"
        found.append(
            HookLocation(
                interface_id=item.interface_id,
                module=item.module,
                entrypoint=item.entrypoint,
                status=status,
                requirement=item.requirement,
                blocks_release=status != "VERIFIED",
            )
        )
    return tuple(found)


def _flag(module: str, name: str) -> bool:
    loaded = importlib.import_module(module)
    value = getattr(loaded, name)
    if type(value) is not bool:
        raise ValueError(f"invalid_flag:{module}.{name}")
    return value


def ai_query_route_default_enabled() -> bool:
    """Read the Terraform variable default. This does not run Terraform."""

    text = AI_QUERY_VARIABLES.read_text(encoding="utf-8")
    marker = 'variable "enable_ai_query_route"'
    start = text.index(marker)
    next_variable = text.find("\nvariable ", start + len(marker))
    block = text[start:] if next_variable < 0 else text[start:next_variable]
    return "default     = true" in block or "default = true" in block


def deployment_acceptance_report() -> AcceptanceReport:
    """Current release gates. A cleared gate is one that no longer blocks enablement."""

    from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
    from wilvor_ai.master_gateway_runtime_boundary import ISSUANCE_CONTROL_IDS

    authenticity_only_unestablished = list(MasterSourceAuthenticity) == [
        MasterSourceAuthenticity.NOT_ESTABLISHED
    ]
    hooks = locate_sprint3d_hooks()
    gates = (
        AcceptanceGate(
            "issuance_controls",
            "Twelve ingress issuance controls are satisfied from operator evidence.",
            False,
            f"unsatisfied:{','.join(ISSUANCE_CONTROL_IDS)}",
        ),
        AcceptanceGate(
            "ingress_attestation",
            "Invocation provenance is attested. The event is not the signature.",
            _flag("wilvor_ai.sprint3c_ingress_principal", "SPRINT3C_INGRESS_ATTESTATION_AVAILABLE"),
            "SPRINT3C_INGRESS_ATTESTATION_AVAILABLE is false",
        ),
        AcceptanceGate(
            "production_principal",
            "Production principal issuance is wired and distinct from synthetic admission.",
            _flag("wilvor_ai.sprint3c_ingress_principal", "SPRINT3C_PRODUCTION_PRINCIPAL_ISSUANCE_ENABLED"),
            "production principal issuance is closed",
        ),
        AcceptanceGate(
            "iam_invoke_review",
            "Same-account lambda:InvokeFunction identity policy is reviewed.",
            _flag("wilvor_ai.sprint3c_ingress_principal", "SPRINT3C_IAM_INVOKE_BOUNDARY_REVIEWED"),
            "same-account IAM invoke boundary is unreviewed",
        ),
        AcceptanceGate(
            "live_aws_deployment_evidence",
            "Live API, account, region, policies, qualifier, function URL, and logs are inspected.",
            _flag("wilvor_ai.sprint3c_ingress_principal", "SPRINT3C_AWS_DEPLOYMENT_EVIDENCE_AVAILABLE"),
            "live AWS deployment evidence is missing",
        ),
        AcceptanceGate(
            "production_grant",
            "Production grant issuance and the signing-key store are integrated.",
            _flag("wilvor_ai.master_execution_grants", "PRODUCTION_GRANT_ISSUANCE_AVAILABLE")
            and _flag("wilvor_ai.master_execution_grants", "PRODUCTION_SIGNING_KEY_STORE_INTEGRATED"),
            "production grant issuance and signing-key store stay closed",
        ),
        AcceptanceGate(
            "replay_prevention",
            "Reviewed signatures prevent replay. A local HMAC flag is not that control.",
            _flag("wilvor_ai.master_execution_grants", "HMAC_PREVENTS_REPLAY"),
            "HMAC_PREVENTS_REPLAY is false",
        ),
        AcceptanceGate(
            "atomic_ledger_consume",
            "One verified ledger consume is integrated. A local transaction is not that proof.",
            _flag("wilvor_ai.master_execution_grants", "DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE")
            and _flag("wilvor_ai.sprint3c_grant_ledger", "SPRINT3C_PRODUCTION_GRANT_LEDGER_ENABLED"),
            "durable master ledger consumption is not integrated",
        ),
        AcceptanceGate(
            "ledger_provenance",
            "Account and table provenance are verified for wilvor_master_execution_ledger.",
            _flag("wilvor_ai.master_execution_ledger", "DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE"),
            "ledger client type is not verified AWS provenance",
        ),
        AcceptanceGate(
            "exactly_once_not_claimed",
            "Exactly-once specialist execution is not claimed from a ledger write.",
            not _flag("wilvor_ai.sprint3c_grant_ledger", "SPRINT3C_DISTRIBUTED_EXACTLY_ONCE"),
            "distributed exactly-once was claimed from a ledger write",
        ),
        AcceptanceGate(
            "production_scoped_read",
            "Production GET_ITEM and QUERY run on an approved adapter.",
            _flag("wilvor_ai.sprint3c_scoped_read", "PRODUCTION_SCOPED_AWS_READ_ENABLED")
            and _flag("wilvor_ai.sprint3c_scoped_read", "LIVE_AWS_OPERATION_PERMITTED")
            and _flag("wilvor_ai.sprint3c_scoped_read", "APPROVED_SOURCE_ADAPTER"),
            "scoped AWS read stays offline and unapproved",
        ),
        AcceptanceGate(
            "source_authenticator",
            "MA4 can represent authenticity other than NOT_ESTABLISHED.",
            (not authenticity_only_unestablished)
            and _flag("wilvor_ai.sprint3c_evidence_response", "PRODUCTION_MA4_ADMISSION_AVAILABLE"),
            "MasterSourceAuthenticity is only NOT_ESTABLISHED",
        ),
        AcceptanceGate(
            "false_operational_claims",
            "Verified zero, low risk, polygon overlap, impact counts, and safety text stay disabled.",
            not any(
                (
                    _flag("wilvor_ai.sprint3c_evidence_response", "EMPTY_PAGE_CREATES_VERIFIED_ZERO"),
                    _flag("wilvor_ai.sprint3c_evidence_response", "ABSENT_RISK_IS_LOW_RISK"),
                    _flag(
                        "wilvor_ai.sprint3c_evidence_response",
                        "REGIONAL_TEXT_ESTABLISHES_POLYGON_INTERSECTION",
                    ),
                    _flag("wilvor_ai.sprint3c_evidence_response", "NUMERICAL_IMPACT_COUNTS_IN_MA5"),
                    _flag(
                        "wilvor_ai.sprint3c_evidence_response",
                        "SAFETY_OR_DIVERSION_INSTRUCTIONS_ENABLED",
                    ),
                )
            ),
            "a false operational-claim flag is enabled",
        ),
        AcceptanceGate(
            "native_production_dispatch",
            "Native production dispatch is enabled for a consumed production grant.",
            _flag("wilvor_ai.master_authorized_scheduler_adapter", "PRODUCTION_DISPATCH_ENABLED")
            and _flag("wilvor_ai.sprint3c_specialist_orchestration", "NATIVE_PRODUCTION_DISPATCH_AVAILABLE"),
            "production dispatch stays disabled",
        ),
        AcceptanceGate(
            "ai_query_route",
            "POST /ai/query stays disabled until a separate reviewed change enables it.",
            not ai_query_route_default_enabled(),
            "enable_ai_query_route default is true",
        ),
        AcceptanceGate(
            "sprint3d_hooks",
            "Expected Sprint 3D modules are present and verified, not merely named.",
            all(item.status == "VERIFIED" for item in hooks),
            "Sprint 3D integration hooks are absent or unverified",
        ),
    )
    ready = all(gate.cleared for gate in gates)
    return AcceptanceReport(
        schema_version=SCHEMA_VERSION,
        ready=ready,
        tier=ExecutionTier.PRODUCTION_RELEASE if ready else ExecutionTier.OFFLINE_SIMULATED,
        gates=gates,
        missing_hooks=hooks,
    )


def evaluate_real_aws_gate(environ: Mapping[str, str]) -> RealAwsGateStatus:
    """Open the real AWS suite only for an explicit nonproduction probe.

    A closed gate is a skip, not a live pass. An open gate without the probe
    module fails closed and still does not call AWS.
    """

    if environ.get(REAL_AWS_WRITES_ENV) == "1":
        return RealAwsGateStatus(
            state=RealAwsGateState.FAIL_CLOSED,
            executed=False,
            live_pass=False,
            reason="AWS writes are refused. This suite does not record a live pass.",
            missing_hooks=(),
        )
    if environ.get(REAL_AWS_GATE_ENV) != "1":
        return RealAwsGateStatus(
            state=RealAwsGateState.CLOSED,
            executed=False,
            live_pass=False,
            reason=(
                "Real AWS nonproduction suite is gated. "
                "A skip is not a successful live AWS test."
            ),
            missing_hooks=(),
        )
    missing: list[str] = []
    account = environ.get(REAL_AWS_ACCOUNT_ENV, "")
    region = environ.get(REAL_AWS_REGION_ENV, "")
    table = environ.get(REAL_AWS_LEDGER_TABLE_ENV, "")
    if len(account) != 12 or not account.isdigit() or account == "000000000000":
        missing.append(REAL_AWS_ACCOUNT_ENV)
    if not region or region == "aws-global":
        missing.append(REAL_AWS_REGION_ENV)
    if table != REQUIRED_LEDGER_TABLE:
        missing.append(REAL_AWS_LEDGER_TABLE_ENV)
    hooks = locate_sprint3d_hooks()
    probe = next(item for item in hooks if item.entrypoint == "probe_nonproduction_readonly")
    ledger = next(item for item in hooks if item.entrypoint == "verify_ledger_account_and_table")
    if probe.status != "VERIFIED":
        missing.append(f"{probe.module}.{probe.entrypoint}")
    if ledger.status != "VERIFIED":
        missing.append(f"{ledger.module}.{ledger.entrypoint}")
    if missing:
        return RealAwsGateStatus(
            state=RealAwsGateState.FAIL_CLOSED,
            executed=False,
            live_pass=False,
            reason=(
                "Real AWS gate is open but the nonproduction probe is not verified. "
                "Refusing to mark a fake or unwired client as a live pass. Missing: "
                + ", ".join(missing)
            ),
            missing_hooks=tuple(missing),
        )
    return RealAwsGateStatus(
        state=RealAwsGateState.READY_TO_PROBE,
        executed=False,
        live_pass=False,
        reason="Probe prerequisites are present. The validation suite still performs no AWS write.",
        missing_hooks=(),
    )
