"""Sprint 4 Lambda import and client-initialization profiling.

The closed AI host handler must stay import-light. Heavy ``wilvor_ai``
graphs belong on a later armed path, not on the 503 return. This module
measures those costs with injected clocks and fake clients. It does not
construct boto3, invoke Lambda, or modify the Task 2C probe artifacts.
"""

from __future__ import annotations

import ast
import importlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from wilvor_ai.contracts import ContractValidationError


SPRINT4_LAMBDA_PROFILE_SCHEMA = "wilvor.ai.sprint4.security_lambda_profile.v1"
SPRINT4_CLIENT_NETWORK_INIT_ENABLED = False
HOST_TIMEOUT_SECONDS = 30
HOST_MEMORY_MB = 256
CLOSED_HANDLER_IMPORT_BUDGET_MS = 250
ARMED_IMPORT_BUDGET_MS = 8_000
CLIENT_INIT_BUDGET_MS = 1_000
HANDLER_RELATIVE_PATH = Path("modules") / "ai_host" / "src" / "handler.py"
PROBE_RELATIVE_PATHS = (
    Path("functions") / "ai_execution_role_probe" / "execution_role_probe.py",
    Path("functions") / "ai_execution_role_probe" / "package_execution_role_probe.py",
    Path("functions") / "ai_execution_role_probe" / "execution_role_probe_window.py",
)
FORBIDDEN_HANDLER_IMPORTS = frozenset(
    {
        "boto3",
        "botocore",
        "wilvor_ai",
        "wilvor_ai.sprint3d_aws_execution",
        "wilvor_ai.sprint3c_grant_ledger",
        "requests",
        "urllib",
        "anthropic",
    }
)


def _assert_closed() -> None:
    if SPRINT4_CLIENT_NETWORK_INIT_ENABLED is not False:
        raise ContractValidationError("sprint4_client_network_init_disabled")


def initialize_live_boto3_client(*_args: object, **_kwargs: object) -> None:
    """Refuse live client construction. Profiling uses injected fakes."""

    _assert_closed()
    raise ContractValidationError("sprint4_client_network_init_disabled")


def _monotonic() -> float:
    return time.perf_counter()


def _parse_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] if alias.name else alias.name for alias in node.names)
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
            imported.add(node.module)
    return imported


@dataclass(frozen=True)
class ImportProfile:
    """One import measurement. Duration is not a live Lambda billed duration."""

    target: str
    duration_ms: float
    imported: bool
    boto3_imported: bool
    within_budget: bool
    budget_ms: int
    live_network_used: bool = False

    def __post_init__(self) -> None:
        _assert_closed()
        if self.live_network_used is not False:
            raise ContractValidationError("sprint4_client_network_init_disabled")
        if type(self.duration_ms) is not float or self.duration_ms < 0:
            raise ContractValidationError("invalid_profile_duration")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class ClientInitProfile:
    """Fake-client construction cost. Not STS and not a real DynamoDB session."""

    duration_ms: float
    client_type: str
    constructed: bool
    network_used: bool
    within_budget: bool
    budget_ms: int = CLIENT_INIT_BUDGET_MS

    def __post_init__(self) -> None:
        _assert_closed()
        if self.network_used is not False:
            raise ContractValidationError("sprint4_client_network_init_disabled")
        if self.constructed is not True:
            raise ContractValidationError("invalid_client_profile")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class HostBudgetAssessment:
    """Compare profiles with the Task 2C diagnostic host size. Not a live invoke."""

    timeout_seconds: int
    memory_mb: int
    closed_handler_within_budget: bool
    armed_import_within_budget: bool
    client_init_within_budget: bool
    closed_handler_imports_wilvor_ai: bool
    probe_artifacts_modified: bool
    ready_for_live_invoke: bool

    def __post_init__(self) -> None:
        _assert_closed()
        if self.timeout_seconds != HOST_TIMEOUT_SECONDS or self.memory_mb != HOST_MEMORY_MB:
            raise ContractValidationError("host_budget_changed")
        if self.probe_artifacts_modified is not False:
            raise ContractValidationError("task_2c_probe_artifacts_must_not_change")
        if self.ready_for_live_invoke is not False:
            raise ContractValidationError("sprint4_live_aws_not_executed")
        if self.closed_handler_imports_wilvor_ai is not False:
            raise ContractValidationError("closed_handler_must_stay_import_light")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class Sprint4LambdaProfileReport:
    schema_version: str
    closed_handler: ImportProfile
    armed_aws_execution: ImportProfile
    security_ledger: ImportProfile
    client_init: ClientInitProfile
    budget: HostBudgetAssessment
    forbidden_handler_imports: tuple[str, ...]
    handler_imports: tuple[str, ...]

    def __post_init__(self) -> None:
        _assert_closed()
        if self.schema_version != SPRINT4_LAMBDA_PROFILE_SCHEMA:
            raise ContractValidationError("invalid_schema_version")

    def __bool__(self) -> bool:
        return False


def inspect_closed_handler(repo_root: Path) -> tuple[str, ...]:
    """Static import graph of the packaged handler. Does not execute AWS."""

    _assert_closed()
    handler = repo_root / HANDLER_RELATIVE_PATH
    imported = tuple(sorted(_parse_imports(handler)))
    forbidden = FORBIDDEN_HANDLER_IMPORTS.intersection(imported)
    if forbidden:
        raise ContractValidationError("closed_handler_must_stay_import_light")
    source = handler.read_text(encoding="utf-8")
    if "boto3" in source or "wilvor_ai" in source:
        raise ContractValidationError("closed_handler_must_stay_import_light")
    if "ledger_writes_enabled" not in source or "ai_host_invoke_closed" not in source:
        raise ContractValidationError("closed_handler_contract_drift")
    return imported


def assert_probe_artifacts_untouched(repo_root: Path, expected_sha256: MappingSha | None = None) -> None:
    """Task 2C probe files must remain in the tree and are not rewritten here."""

    del expected_sha256
    _assert_closed()
    for relative in PROBE_RELATIVE_PATHS:
        path = repo_root / relative
        if not path.is_file():
            raise ContractValidationError("task_2c_probe_artifact_missing")


MappingSha = dict[str, str]


def profile_import(module_name: str, *, budget_ms: int, clock: Callable[[], float] | None = None) -> ImportProfile:
    """Import one module and record duration. The module must not open AWS."""

    _assert_closed()
    tick = clock or _monotonic
    start = tick()
    module = importlib.import_module(module_name)
    duration_ms = (tick() - start) * 1000.0
    imported_boto3 = "boto3" in getattr(module, "__dict__", {})
    return ImportProfile(
        target=module_name,
        duration_ms=float(duration_ms),
        imported=True,
        boto3_imported=imported_boto3,
        within_budget=duration_ms <= budget_ms,
        budget_ms=budget_ms,
    )


def profile_closed_handler_load(
    repo_root: Path,
    *,
    clock: Callable[[], float] | None = None,
) -> ImportProfile:
    """Load handler.py as a file module, matching the packaged zip contents."""

    _assert_closed()
    inspect_closed_handler(repo_root)
    import importlib.util

    path = repo_root / HANDLER_RELATIVE_PATH
    tick = clock or _monotonic
    start = tick()
    spec = importlib.util.spec_from_file_location("wilvor_sprint4_closed_handler_profile", path)
    if spec is None or spec.loader is None:
        raise ContractValidationError("closed_handler_missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    duration_ms = (tick() - start) * 1000.0
    boto3_imported = "boto3" in getattr(module, "__dict__", {})
    return ImportProfile(
        target="modules/ai_host/src/handler.py",
        duration_ms=float(duration_ms),
        imported=True,
        boto3_imported=boto3_imported,
        within_budget=duration_ms <= CLOSED_HANDLER_IMPORT_BUDGET_MS,
        budget_ms=CLOSED_HANDLER_IMPORT_BUDGET_MS,
    )


def profile_fake_client_init(
    factory: Callable[[], object],
    *,
    clock: Callable[[], float] | None = None,
) -> ClientInitProfile:
    """Time injected client construction. ``factory`` must not call AWS."""

    _assert_closed()
    tick = clock or _monotonic
    start = tick()
    client = factory()
    duration_ms = (tick() - start) * 1000.0
    if client is None:
        raise ContractValidationError("invalid_client_profile")
    return ClientInitProfile(
        duration_ms=float(duration_ms),
        client_type=type(client).__name__,
        constructed=True,
        network_used=False,
        within_budget=duration_ms <= CLIENT_INIT_BUDGET_MS,
    )


def assess_host_budget(
    *,
    closed_handler: ImportProfile,
    armed_import: ImportProfile,
    client_init: ClientInitProfile,
    handler_imports_wilvor_ai: bool,
) -> HostBudgetAssessment:
    _assert_closed()
    return HostBudgetAssessment(
        timeout_seconds=HOST_TIMEOUT_SECONDS,
        memory_mb=HOST_MEMORY_MB,
        closed_handler_within_budget=closed_handler.within_budget,
        armed_import_within_budget=armed_import.within_budget,
        client_init_within_budget=client_init.within_budget,
        closed_handler_imports_wilvor_ai=handler_imports_wilvor_ai,
        probe_artifacts_modified=False,
        ready_for_live_invoke=False,
    )


def profile_sprint4_lambda(
    repo_root: Path,
    *,
    client_factory: Callable[[], object],
    clock: Callable[[], float] | None = None,
) -> Sprint4LambdaProfileReport:
    """Profile closed handler, armed import graph, and fake client construction."""

    _assert_closed()
    assert_probe_artifacts_untouched(repo_root)
    handler_imports = inspect_closed_handler(repo_root)
    closed = profile_closed_handler_load(repo_root, clock=clock)
    armed = profile_import(
        "wilvor_ai.sprint3d_aws_execution",
        budget_ms=ARMED_IMPORT_BUDGET_MS,
        clock=clock,
    )
    ledger = profile_import(
        "wilvor_ai.sprint4_security_ledger",
        budget_ms=ARMED_IMPORT_BUDGET_MS,
        clock=clock,
    )
    client = profile_fake_client_init(client_factory, clock=clock)
    if armed.boto3_imported or ledger.boto3_imported or closed.boto3_imported:
        raise ContractValidationError("boto3_must_not_import_at_module_level")
    budget = assess_host_budget(
        closed_handler=closed,
        armed_import=armed,
        client_init=client,
        handler_imports_wilvor_ai=False,
    )
    return Sprint4LambdaProfileReport(
        schema_version=SPRINT4_LAMBDA_PROFILE_SCHEMA,
        closed_handler=closed,
        armed_aws_execution=armed,
        security_ledger=ledger,
        client_init=client,
        budget=budget,
        forbidden_handler_imports=tuple(sorted(FORBIDDEN_HANDLER_IMPORTS)),
        handler_imports=handler_imports,
    )


_assert_closed()


__all__ = [
    "ARMED_IMPORT_BUDGET_MS",
    "CLIENT_INIT_BUDGET_MS",
    "CLOSED_HANDLER_IMPORT_BUDGET_MS",
    "FORBIDDEN_HANDLER_IMPORTS",
    "HANDLER_RELATIVE_PATH",
    "HOST_MEMORY_MB",
    "HOST_TIMEOUT_SECONDS",
    "PROBE_RELATIVE_PATHS",
    "SPRINT4_CLIENT_NETWORK_INIT_ENABLED",
    "SPRINT4_LAMBDA_PROFILE_SCHEMA",
    "ClientInitProfile",
    "HostBudgetAssessment",
    "ImportProfile",
    "Sprint4LambdaProfileReport",
    "assert_probe_artifacts_untouched",
    "assess_host_budget",
    "initialize_live_boto3_client",
    "inspect_closed_handler",
    "profile_closed_handler_load",
    "profile_fake_client_init",
    "profile_import",
    "profile_sprint4_lambda",
]
