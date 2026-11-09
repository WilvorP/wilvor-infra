"""Python dependency ownership, reclassification and fail-closed audit checks."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import ci_security as security


@pytest.fixture
def manifests(tmp_path, monkeypatch):
    original = security.ROOT
    files = [Path(name) for name in sorted(security.REQUIRED_PYTHON_MANIFESTS)]
    for path in files:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((original / path).read_bytes())
    monkeypatch.setattr(security, "ROOT", tmp_path)
    monkeypatch.setattr(security, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(security, "tracked_files", lambda: files)
    return files


def test_every_existing_manifest_is_owned_once(manifests):
    paths = security.python_manifest_paths()
    assert len(paths) == len(set(paths)) == 12
    assert {path.as_posix() for path in paths} == security.REQUIRED_PYTHON_MANIFESTS
    assert security.requirement_lines(security.ROOT / "requirements.txt")


@pytest.mark.parametrize("name", ["requirements-dev.txt", "tests/requirements-extra.txt", "functions/new_lambda/requirements.txt"])
def test_new_supported_manifests_are_automatically_discovered(manifests, name):
    manifests.append(Path(name))
    assert Path(name) in security.python_manifest_paths()


@pytest.mark.parametrize("name", ["docs/requirements.txt", "tests/fixtures/requirements.txt", "pyproject.toml", "Pipfile", "uv.lock", "setup.py"])
def test_unclassified_manifests_cannot_be_silently_ignored(manifests, name):
    manifests.append(Path(name))
    with pytest.raises(RuntimeError, match="Unclassified"):
        security.python_manifest_paths()


@pytest.mark.parametrize("name", ["requirements.txt", "tests/requirements-ci.txt", "functions/encounter/processor/requirements.txt"])
def test_missing_or_renamed_owners_require_review(manifests, name):
    manifests.remove(Path(name))
    manifests.append(Path(name).with_name("retired-snapshot.txt"))
    with pytest.raises(RuntimeError, match="required Python manifest"):
        security.python_manifest_paths()


@pytest.mark.parametrize("replacement", ["# historical snapshot; ignore me\n", "certifi>=2026\n", "--no-deps\n", "-r unclassified.txt\n", "certifi @ HTTPS://example.invalid/package.whl\n"])
def test_snapshot_cannot_be_reclassified_to_bypass_scanning(manifests, tmp_path, monkeypatch, replacement):
    (security.ROOT / "requirements.txt").write_text(replacement, encoding="utf-16")
    # Root comes after Lambda manifests; isolate the root validation here.
    monkeypatch.setattr(security, "python_manifest_paths", lambda: [Path("requirements.txt")])
    monkeypatch.setattr(security, "invoke", lambda *a, **kw: pytest.fail("Unsafe snapshot must fail before audit"))
    with pytest.raises(RuntimeError):
        security.scan_python(tmp_path)


@pytest.mark.parametrize("name", ["tests/requirements-test.txt", "functions/encounter/processor/requirements.txt"])
def test_only_reviewed_stdlib_manifest_can_be_empty(manifests, tmp_path, monkeypatch, name):
    (security.ROOT / name).write_text("# stdlib-only\n", encoding="utf-8")
    monkeypatch.setattr(security, "python_manifest_paths", lambda: [Path(name)])
    with pytest.raises(RuntimeError, match="stdlib-only"):
        security.scan_python(tmp_path)


@pytest.mark.parametrize("outcome", ["clean", "vulnerable", "unaudited", "service-error", "exit-mismatch", "empty-response"])
def test_audit_modes_and_findings_propagate_to_ci(manifests, monkeypatch, outcome):
    audited = []

    def invoke(command):
        target = Path(command[command.index("-r") + 1])
        root = target.name == "developer-snapshot.txt"
        assert "--strict" in command and "--aliases=on" in command
        assert not any("ignore-vuln" in str(arg) for arg in command)
        assert ("--no-deps" in command) == root
        assert ("--disable-pip" in command) == root
        audited.append(root)
        report = Path(command[command.index("--output") + 1])
        dependency = {"name": "certifi", "version": "2026.10.18", "vulns": []}
        if root and outcome == "vulnerable":
            dependency.update(version="2026.6.17", vulns=[{"id": "PYSEC-2026-4320", "fix_versions": ["2026.10.18"]}])
        if root and outcome == "unaudited":
            dependency["skip_reason"] = "unsupported"
        report.write_text(json.dumps({"dependencies": [] if root and outcome == "empty-response" else [dependency]}), encoding="utf-8")
        rc = 2 if root and outcome == "service-error" else 1 if root and outcome in {"vulnerable", "exit-mismatch"} else 0
        return SimpleNamespace(returncode=rc)

    monkeypatch.setattr(security, "invoke", invoke)
    expected = 0 if outcome == "clean" else 1 if outcome == "vulnerable" else 2
    assert security.run("python-dependencies") == expected
    report = json.loads((security.REPORTS / "python-dependencies" / "scan.json").read_text())
    assert len(report["contexts"]) == 12
    assert len(audited) == 11 and sum(audited) == 1
    assert {c["manifest"] for c in report["contexts"]} == security.REQUIRED_PYTHON_MANIFESTS
    assert next(c for c in report["contexts"] if c["manifest"] == "requirements.txt")["mode"] == "declared-pinned-snapshot"
    if expected:
        assert report["status"] != "passed"
        reports = [security.passed_scan_report(name) for name in security.SCANS if name != "python-dependencies"]
        with pytest.raises(ValueError):
            security.validate_security_reports(reports + [report])
