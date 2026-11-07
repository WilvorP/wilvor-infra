"""Only the five reviewed history fingerprints may bypass secret detection."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import ci, ci_security as security


REVIEWED = (
    ("c191249913ecb8a91f10ed41d90dbd439dbaa8bd", "tests/contracts/test_sprint3c_scoped_read.py", 598),
    ("c191249913ecb8a91f10ed41d90dbd439dbaa8bd", "tests/contracts/test_sprint3c_scoped_read.py", 600),
    ("8f801061fa3cf5efc07ebfe7139fed4e09678abe", "tests/contracts/test_master_specialist_runtime_contract.py", 132),
    ("9a003c2e54c88a11dcc0d9f753f1453dbff724cb", "dashboard/src/api/operationalApi.test.ts", 114),
    ("9a003c2e54c88a11dcc0d9f753f1453dbff724cb", "dashboard/src/api/operationalApi.test.ts", 195),
)


@pytest.mark.parametrize("commit,file,line", REVIEWED)
def test_only_reviewed_scanner_fingerprints_are_approved(commit, file, line):
    entries = security.validate_gitleaksignore()
    assert len(entries) == len(set(entries)) == 5
    assert set(entries) == {f"{c}:{f}:generic-api-key:{n}" for c, f, n in REVIEWED}
    assert f"{commit}:{file}:generic-api-key:{line}" in entries


@pytest.mark.parametrize("mutation", ["extra", "duplicate", "missing", "line", "commit", "rule", "path", "glob", "inline-comment", "empty"])
def test_ignore_file_cannot_expand_or_change_exceptions(tmp_path, mutation):
    entries = list(security.validate_gitleaksignore())
    if mutation == "extra":
        entries.append("unapproved-finding")
    elif mutation == "duplicate":
        entries.append(entries[0])
    elif mutation == "missing":
        entries.pop()
    elif mutation == "empty":
        entries.clear()
    else:
        parts = entries[0].split(":")
        if mutation == "line":
            parts[-1] = str(int(parts[-1]) + 1)
        elif mutation == "commit":
            parts[0] = "0" * 40
        elif mutation == "rule":
            parts[-2] = "other-rule"
        elif mutation == "path":
            parts[1] = "tests/other.py"
        elif mutation == "glob":
            parts[1] = "tests/**"
        else:
            parts[-1] += " # unreviewed suffix"
        entries[0] = ":".join(parts)
    path = tmp_path / ".gitleaksignore"
    path.write_text("\n".join(entries), encoding="utf-8")
    with pytest.raises(ValueError, match="five reviewed"):
        security.validate_gitleaksignore(path)


def test_invalid_exceptions_stop_scanning_before_invocation(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "ROOT", tmp_path)
    (tmp_path / ".gitleaksignore").write_text("unapproved", encoding="utf-8")
    monkeypatch.setattr(security, "invoke", lambda *a, **kw: pytest.fail("Invalid exceptions must fail before invoking Git/scanner"))
    with pytest.raises(ValueError):
        security.scan_secrets(tmp_path)


def test_workflow_policy_rejects_invalid_exception_file(monkeypatch):
    def reject():
        raise ValueError("five reviewed fingerprints required")
    monkeypatch.setattr(ci, "validate_gitleaksignore", reject)
    with pytest.raises(ValueError, match="five reviewed"):
        ci.check_workflows()


@pytest.mark.parametrize("outcome", ["clean", "history-finding", "working-tree-finding", "scanner-error", "exit-mismatch"])
def test_scanner_cannot_bypass_detection_or_offline_safeguards(tmp_path, monkeypatch, outcome):
    entries = security.validate_gitleaksignore()
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitleaksignore").write_text("\n".join(entries), encoding="utf-8")
    monkeypatch.setattr(security, "ROOT", root)
    monkeypatch.setattr(security, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(security, "tracked_files", lambda: [Path(".gitleaksignore")])
    monkeypatch.setenv("GITLEAKS_CONFIG", "unreviewed-config")
    monkeypatch.setenv("AWS_PROFILE", "unreviewed-profile")
    calls = []

    def fake_invoke(command, *, env):
        if command[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout="false\n")
        scope = "history" if command[1] == "git" else "working-tree"
        calls.append(scope)
        assert "GITLEAKS_CONFIG" not in env and "AWS_PROFILE" not in env
        assert env["AWS_EC2_METADATA_DISABLED"] == "true"
        assert "--redact=100" in command
        assert "--ignore-gitleaks-allow" in command and "--exit-code=1" in command
        config = Path(command[command.index("--config") + 1])
        assert config.read_text() == "[extend]\nuseDefault = true\n"
        ignored = Path(command[command.index("--gitleaks-ignore-path") + 1])
        if scope == "history":
            assert "--log-opts=--all" in command
            assert set(ignored.read_text().splitlines()) == set(entries)
        else:
            assert command[1] == "dir"
            assert ignored.read_text() == ""  # No current-tree exceptions.
        report = Path(next(str(arg).split("=", 1)[1] for arg in command if str(arg).startswith("--report-path=")))
        finding = outcome == f"{scope}-finding"
        raw = []
        if finding:
            file = "new-fixture.txt" if scope == "history" else str(report.parent / "source" / "new-fixture.txt")
            raw = [{"RuleID": "generic-api-key", "File": file, "StartLine": 1, "Secret": "DO_NOT_PUBLISH"}]
        report.write_text(json.dumps(raw), encoding="utf-8")
        rc = 2 if outcome == "scanner-error" else 1 if finding or outcome == "exit-mismatch" else 0
        return SimpleNamespace(returncode=rc, stdout="")

    monkeypatch.setattr(security, "invoke", fake_invoke)
    expected = 0 if outcome == "clean" else 1 if outcome.endswith("-finding") else 2
    assert security.run("secrets") == expected
    report = json.loads((security.REPORTS / "secrets" / "scan.json").read_text())
    assert "DO_NOT_PUBLISH" not in json.dumps(report)
    assert calls == (["history"] if expected == 2 else ["history", "working-tree"])
    reports = [{"schema": 1, "scan": name, "version": security.VERSIONS[name], "status": "passed",
                "exit_code": 0, "findings": [], "contexts": [{"status": "complete"}]}
               for name in security.SCANS if name != "secrets"] + [report]
    if expected == 0:
        security.validate_security_reports(reports)
        assert report["contexts"][0]["approved_history_exceptions"] == 5
    else:
        with pytest.raises(ValueError):
            security.validate_security_reports(reports)
