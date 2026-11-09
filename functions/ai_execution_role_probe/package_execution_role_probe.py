"""Build an isolated zip for a future execution-role probe.

The zip is not the AI host archive. Destinations under ``modules/ai_host``
and the existing function deployment archives are refused before any write.
``boto3`` stays in the Lambda runtime. Model libraries are a packaging error.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = REPO_ROOT / "functions" / "ai_execution_role_probe" / "execution_role_probe.py"
SHARED_ROOT = REPO_ROOT / "functions" / "shared"
FORBIDDEN_DESTINATION_SUFFIXES = (
    "functions/aircraft_current_state_writer/dist/aircraft_current_state_writer.zip",
    "functions/aircraft_raw_processor/dist/aircraft_raw_processor.zip",
    "functions/opensky_poller/dist/opensky_poller.zip",
    "modules/ai_host/dist/ai_host.zip",
    "modules/ai_host/src/handler.py",
)
LIVE_TEST_ZIP_NAME = "wilvor-dev-ai-host-execution-role-readonly-probe-live.zip"
CLOSED_ASSIGNMENT = "PROBE_LIVE_CALLS_ENABLED = False"
LIVE_ASSIGNMENT = "PROBE_LIVE_CALLS_ENABLED = True"
RUNTIME_PROVIDED = frozenset({"boto3", "botocore"})
PACKAGING_REFUSED = frozenset({"langgraph", "anthropic", "openai"})
REFUSED_WILVOR_MODULES = frozenset(
    {
        "wilvor_ai.master_orchestration",
        "wilvor_ai.providers",
        "wilvor_ai.providers.anthropic_live_ops_messages",
        "wilvor_ai.providers.anthropic_decision_messages",
    }
)


class ProbePackageRefusal(RuntimeError):
    """The isolated archive would be unsafe or incomplete."""


def _posix(path: Path) -> str:
    return path.as_posix().casefold()


def assert_destination_allowed(destination: Path) -> None:
    text = _posix(destination)
    for suffix in FORBIDDEN_DESTINATION_SUFFIXES:
        if text.endswith(suffix.casefold()):
            raise ProbePackageRefusal(f"destination_refused:{suffix}")
    parts = [part.casefold() for part in destination.parts]
    if "ai_host" in parts and "modules" in parts:
        raise ProbePackageRefusal("destination_refused:modules/ai_host")


def _is_local(module: str) -> bool:
    root = module.split(".", 1)[0]
    return (SHARED_ROOT / root).is_dir()


def _module_path(module: str) -> Path | None:
    if not _is_local(module):
        return None
    base = SHARED_ROOT.joinpath(*module.split("."))
    file_path = base.with_suffix(".py")
    if file_path.is_file():
        return file_path
    init = base / "__init__.py"
    if init.is_file():
        return init
    return None


def _is_package(module: str) -> bool:
    path = _module_path(module)
    return path is not None and path.name == "__init__.py"


def _from_import(current: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    parts = current.split(".")
    base = parts if _is_package(current) else parts[:-1]
    keep = len(base) - (node.level - 1)
    if keep < 0:
        return None
    parent = base[:keep]
    if node.module is None:
        return ".".join(parent) if parent else None
    return ".".join([*parent, *node.module.split(".")])


def _imports(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and (node.module is not None or node.level):
            name = _from_import(module, node)
            if name:
                found.add(name)
    return found


def _root_name(module: str) -> str:
    return module.split(".", 1)[0]


def source_modules(probe_path: Path = PROBE_PATH) -> tuple[str, ...]:
    """Transitive ``wilvor_ai`` modules required to import the probe."""

    if probe_path.resolve() == (REPO_ROOT / "modules" / "ai_host" / "src" / "handler.py").resolve():
        raise ProbePackageRefusal("refusing_to_package_the_deployed_handler")
    pending = {("wilvor_execution_role_probe", probe_path)}
    seen_paths: set[Path] = set()
    local: set[str] = set()
    while pending:
        module, path = pending.pop()
        if path in seen_paths:
            continue
        seen_paths.add(path)
        if _is_local(module):
            if module in REFUSED_WILVOR_MODULES or module.startswith("wilvor_ai.providers"):
                raise ProbePackageRefusal(f"model_module_refused:{module}")
            local.add(module)
        current = module if _is_local(module) else "wilvor_execution_role_probe"
        for imported in _imports(path, current):
            root = _root_name(imported)
            if root in PACKAGING_REFUSED or imported in REFUSED_WILVOR_MODULES or imported.startswith("wilvor_ai.providers"):
                raise ProbePackageRefusal(f"model_module_refused:{imported}")
            if root in RUNTIME_PROVIDED or not _is_local(imported):
                continue
            dependency = _module_path(imported)
            if dependency is None:
                raise ProbePackageRefusal(f"missing_module:{imported}")
            pending.add((imported, dependency))
            parent = imported.rsplit(".", 1)[0]
            while parent:
                parent_path = _module_path(parent)
                if parent_path is not None:
                    pending.add((parent, parent_path))
                if "." not in parent:
                    break
                parent = parent.rsplit(".", 1)[0]
    if "wilvor_ai.sprint3d_aws_execution" not in local:
        raise ProbePackageRefusal("agent_c_module_missing")
    if "wilvor_ai.master_orchestration" in local:
        raise ProbePackageRefusal("model_module_refused:wilvor_ai.master_orchestration")
    return tuple(sorted(local))


def _refuse_credential_fallback(text: str) -> None:
    if text.count(CLOSED_ASSIGNMENT) != 1 or LIVE_ASSIGNMENT in text:
        raise ProbePackageRefusal("live_constant_must_stay_false")
    if "profile_name" in text or "assume_role(" in text:
        raise ProbePackageRefusal("credential_fallback_refused")


def _write_archive(destination: Path, handler: bytes, modules: tuple[str, ...]) -> tuple[str, ...]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    archive_names: list[str] = []
    handler = handler.replace(b"\r\n", b"\n")
    with zipfile.ZipFile(destination, "w") as archive:
        info = zipfile.ZipInfo("handler.py")
        info.date_time = (2026, 11, 1, 0, 0, 0)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, handler)
        archive_names.append("handler.py")
        for module in modules:
            path = _module_path(module)
            if path is None:
                raise ProbePackageRefusal(f"missing_module:{module}")
            relative = path.relative_to(SHARED_ROOT).as_posix()
            entry = zipfile.ZipInfo(relative)
            entry.date_time = (2026, 11, 1, 0, 0, 0)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, path.read_bytes())
            archive_names.append(relative)
    if "modules/ai_host/src/handler.py" in archive_names:
        raise ProbePackageRefusal("deployed_handler_was_packaged")
    return tuple(archive_names)


def _checksums(payload: bytes) -> dict[str, str]:
    digest = hashlib.sha256(payload).digest()
    return {
        "sha256": digest.hex(),
        "code_sha256": base64.b64encode(digest).decode("ascii"),
    }


def build_isolated_zip(destination: Path, *, probe_path: Path = PROBE_PATH) -> dict[str, object]:
    """Write the closed probe zip. Refuses the deployed archive paths before opening them."""

    assert_destination_allowed(destination)
    if destination.name == LIVE_TEST_ZIP_NAME:
        raise ProbePackageRefusal("live_zip_requires_explicit_builder")
    text = probe_path.read_text(encoding="utf-8")
    _refuse_credential_fallback(text)
    modules = source_modules(probe_path)
    names = _write_archive(destination, probe_path.read_bytes(), modules)
    return {
        "path": str(destination),
        "files": names,
        "live_constant": False,
        "module_count": len(modules),
    }


def build_live_test_zip(destination: Path, *, probe_path: Path = PROBE_PATH) -> dict[str, object]:
    """Write the one live-test zip. The source file stays closed.

    Only ``handler.py`` inside this archive has the live assignment. The
    source probe file is read twice and must be unchanged.
    """

    assert_destination_allowed(destination)
    if destination.name != LIVE_TEST_ZIP_NAME:
        raise ProbePackageRefusal("live_zip_name_required")
    before = probe_path.read_bytes()
    text = before.decode("utf-8")
    _refuse_credential_fallback(text)
    handler = text.replace(CLOSED_ASSIGNMENT, LIVE_ASSIGNMENT, 1).encode("utf-8")
    if CLOSED_ASSIGNMENT.encode("utf-8") in handler or handler.count(LIVE_ASSIGNMENT.encode("utf-8")) != 1:
        raise ProbePackageRefusal("live_assignment_failed")
    modules = source_modules(probe_path)
    if probe_path.read_bytes() != before:
        raise ProbePackageRefusal("source_was_modified")
    names = _write_archive(destination, handler, modules)
    payload = destination.read_bytes()
    checksums = _checksums(payload)
    manifest = {
        "artifact": LIVE_TEST_ZIP_NAME,
        "source_live_constant": False,
        "handler_live_constant": True,
        "sha256": checksums["sha256"],
        "code_sha256": checksums["code_sha256"],
        "account_id": "396037508959",
        "region": "us-west-1",
        "function_name": "wilvor-dev-ai-host",
        "role_name": "wilvor-dev-ai-host-lambda-role",
        "table_name": "wilvor-dev-aircraft-current-state",
        "aircraft_id": "WILVOR-SPRINT3D-READONLY-PROBE",
        "deployed": False,
    }
    manifest_path = destination.with_name(destination.stem + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if probe_path.read_bytes() != before:
        raise ProbePackageRefusal("source_was_modified")
    return {
        "path": str(destination),
        "manifest_path": str(manifest_path),
        "files": names,
        "live_constant": True,
        "source_live_constant": False,
        "module_count": len(modules),
        **checksums,
    }
