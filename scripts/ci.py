"""CI orchestration, reporting and fail-closed accounting; no deployment code."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ci_groups import GROUPS, MATRIX_GROUPS, expected, inventory

REPORTS = ROOT / "test-results" / "ci"
PINS = {
    "actions/checkout": "11bd71901bbe5b1630ceea73d27597364c9af683",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/setup-node": "49933ea5288caeca8642d1e84afbd3f7d6820020",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "hashicorp/setup-terraform": "b9cd54a3c349d3f38e8881555d616ced269862dd",
}


def read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), object_pairs_hook=unique)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def summary(text):
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(text + "\n")


def clean_junit(path):
    if not Path(path).is_file():
        return
    tree = ET.parse(path)
    for element in tree.iter():
        for child in list(element):
            if child.tag in {"system-out", "system-err"}:
                element.remove(child)
        if element.text:
            element.text = re.sub(r"(?:sk-ant-[\w-]+|AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16})", "[REDACTED]", element.text)
        if "message" in element.attrib:
            element.attrib["message"] = re.sub(r"(?:sk-ant-[\w-]+|AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16})", "[REDACTED]", element.attrib["message"])
    tree.write(path, encoding="utf-8", xml_declaration=True)


def discover(profile, path):
    path = Path(path).resolve()
    result = subprocess.call([
        sys.executable, "-B", "scripts/run_offline_tests.py", "tests", "--collect-only", "-qq",
        "-p", "tests.ci_plugin", f"--ci-inventory={path}", f"--ci-profile={profile}",
    ], cwd=ROOT)
    if result:
        return result
    data = read(path)
    for group in GROUPS:
        if not data["groups"][group]:
            raise ValueError(f"Empty required ownership group: {group}")
    matrix = {"group": [g for g in MATRIX_GROUPS if expected(data, g)]}
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write("matrix=" + json.dumps(matrix) + "\n")
    rows = "\n".join(f"| {g} | {len(data['groups'][g])} | {len(expected(data,g))} |" for g in GROUPS)
    exclusions = ", ".join(f"{key}: {len(value)}" for key, value in data["excluded"].items())
    summary(f"### Test discovery ({profile})\n| Group | Offline cases | Selected |\n|---|---:|---:|\n{rows}\n\nExplicit Step 2 exclusions: {exclusions}.\n")
    return 0


def run_group(group, inventory_path):
    data = read(inventory_path)
    wanted = expected(data, group)
    if not wanted:
        raise ValueError(f"No selected cases for {group}")
    files = sorted({nodeid.split("::", 1)[0] for nodeid in wanted})
    directory = REPORTS / group
    directory.mkdir(parents=True, exist_ok=True)
    junit = directory / "junit.xml"
    environment = os.environ.copy()
    environment["COVERAGE_FILE"] = str(directory / "coverage.data")
    coverage_args = []
    if data["profile"] == "full":
        coverage_args = ["-p", "pytest_cov.plugin", "--cov=functions", "--cov=scripts", "--cov-branch",
                         "--cov-report=term", f"--cov-report=xml:{directory / 'coverage.xml'}"]
    try:
        result = subprocess.call([
            sys.executable, "-B", "scripts/run_offline_tests.py", *files,
            "-q", "-ra", "--tb=short", "-p", "tests.ci_plugin",
            f"--ci-group={group}", f"--ci-expected={Path(inventory_path).resolve()}",
            f"--ci-results={directory / 'results.json'}", f"--junitxml={junit}",
            *coverage_args,
        ], cwd=ROOT, env=environment)
    finally:
        clean_junit(junit)
    report = read(directory / "results.json") if (directory / "results.json").exists() else {"cases": {}}
    counts = {status: list(report["cases"].values()).count(status) for status in ("passed", "failed", "skipped")}
    summary(f"### {group}\nExpected: {len(wanted)}; observed: {counts}. Pytest exit: {result}.\n")
    if set(report["cases"]) != set(wanted) or any(s != "passed" for s in report["cases"].values()):
        return 1
    return result


def policy(document):
    if document.get("permissions") != {"contents": "read"}:
        raise ValueError("Workflow permissions must be contents: read only")
    events = document.get("on", {})
    if set(events) != {"push", "pull_request", "workflow_dispatch"}:
        raise ValueError("Unexpected or missing trigger")
    for event in ("push", "pull_request"):
        if events[event] != {"branches": ["main"]}:
            raise ValueError(f"{event} must target main without filters that omit checks")
    if document.get("env", {}).get("CI_PROFILE") != "${{ github.event_name == 'pull_request' && 'pr' || 'full' }}":
        raise ValueError("Only PR events may select the lightweight profile")
    jobs = document["jobs"]
    if set(jobs) != {"discovery", "python", "terraform", "dashboard", "quality-gate"}:
        raise ValueError("Unexpected workflow job structure")
    gate = jobs["quality-gate"]
    if set(gate["needs"]) != set(jobs) - {"quality-gate"} or gate.get("if", "").strip() not in {"always()", "${{ always() }}"}:
        raise ValueError("Quality gate must always depend on every job family")
    encoded = json.dumps(document)
    if re.search(r"secrets\.|id-token|pull_request_target|--local", encoded, re.I):
        raise ValueError("Secrets, OIDC, privileged events or local bypass are forbidden")
    for name, job in jobs.items():
        if job.get("permissions", {"contents": "read"}) != {"contents": "read"}:
            raise ValueError(f"Excess permissions in {name}")
        if job.get("continue-on-error"):
            raise ValueError("Failure suppression is forbidden")
        for step in job.get("steps", []):
            if step.get("continue-on-error"):
                raise ValueError("Failure suppression is forbidden")
            if "uses" in step:
                action, _, revision = step["uses"].partition("@")
                if PINS.get(action) != revision:
                    raise ValueError(f"Unapproved/unpinned action: {step['uses']}")
                if action == "actions/checkout" and step.get("with", {}).get("persist-credentials") is not False:
                    raise ValueError("Checkout must not persist credentials")
            command = step.get("run", "")
            if "${{" in command:
                raise ValueError("Pass expression values through environment, never shell interpolation")
            if re.search(r"\bterraform(?:\.exe)?\s+(?:plan|apply|destroy)\b|\baws\s+|\bsso\b", command, re.I):
                raise ValueError("Cloud/deployment command is forbidden")
            if name == "dashboard" and (command.startswith("node node_modules/vitest/") or
                                        command.startswith("npm.cmd ") and not command.startswith("npm.cmd ci ")):
                if step.get("env", {}).get("NODE_OPTIONS") != "--require=./scripts/offline.cjs":
                    raise ValueError("Dashboard execution must retain its offline guard")


def check_workflows():
    import yaml
    files = sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    if len(files) != 1:
        raise ValueError("Review security policy before adding another workflow")
    policy(yaml.safe_load(files[0].read_text(encoding="utf-8")))
    summary("### Workflow policy\nTrigger, permission, action-pin and aggregate-gate checks passed.\n")
    return 0


def dashboard_cases(listed, raw):
    def key(file, name):
        normalized = file.replace("\\", "/").split("/dashboard/")[-1]
        return normalized + "::" + name
    wanted = [key(case["file"], case["name"]) for case in listed]
    cases = {}
    for file in raw["testResults"]:
        for case in file["assertionResults"]:
            # Vitest list uses ' > ' but its JSON fullName uses spaces.
            name = " > ".join([*case["ancestorTitles"], case["title"]])
            nodeid = key(file["name"], name)
            if nodeid in cases:
                raise ValueError(f"Duplicate dashboard test: {nodeid}")
            cases[nodeid] = case["status"]
    if len(wanted) != len(set(wanted)) or set(wanted) != set(cases) or not cases:
        raise ValueError("Dashboard collection/execution mismatch")
    return cases


def dashboard_report(profile):
    directory = REPORTS / "dashboard"
    if profile == "pr":
        summary("### Dashboard PR profile\nESLint/TypeScript are required job steps; tests/build are explicitly deferred to the full profile.\n")
        return 0
    raw = read(directory / "vitest.json")
    cases = dashboard_cases(read(directory / "expected.json"), raw)
    write(directory / "results.json", {"group": "dashboard", "exit_code": 0 if raw["success"] else 1, "cases": cases})
    clean_junit(directory / "junit.xml")
    write(directory / "coverage-summary.json", read(directory / "coverage" / "coverage-summary.json"))
    summary(f"### Dashboard\nCollected and executed {len(cases)} cases. Coverage is reported without an arbitrary threshold.\n")
    return 0 if raw["success"] and all(s == "passed" for s in cases.values()) else 1


def validate_gate(data, reports, profile, needs):
    if data != inventory([n for values in data["groups"].values() for n in values] +
                         [n for values in data["excluded"].values() for n in values], profile):
        raise ValueError("Inventory ownership/profile integrity mismatch")
    if set(needs) != {"discovery", "python", "terraform", "dashboard"} or any(v["result"] != "success" for v in needs.values()):
        raise ValueError("A required job failed, was cancelled, or was skipped")
    wanted_groups = {g for g in GROUPS if expected(data, g)}
    if profile == "full":
        wanted_groups.add("dashboard")
    actual = {}
    for report in reports:
        group = report["group"]
        if group in actual:
            raise ValueError(f"Duplicate result artifact for {group}")
        actual[group] = report
    if set(actual) != wanted_groups:
        raise ValueError(f"Missing/unexpected result groups: expected={wanted_groups}, actual={set(actual)}")
    observed = set()
    for group, report in actual.items():
        cases = report["cases"]
        if report["exit_code"] or not cases or any(status != "passed" for status in cases.values()):
            raise ValueError(f"Failed, skipped or missing execution in {group}")
        if group != "dashboard":
            if set(cases) != set(expected(data, group)):
                raise ValueError(f"Missing/unexpected cases in {group}")
            if observed.intersection(cases):
                raise ValueError("A Python test executed in multiple groups")
            observed.update(cases)
    return len(observed)


def gate(args):
    needs = json.loads(os.environ["NEEDS_JSON"])
    summary("### Required jobs\n" + "\n".join(f"- {name}: {value['result']}" for name, value in needs.items()))
    if any(value["result"] != "success" for value in needs.values()):
        raise ValueError("A required job failed, was cancelled, or was skipped")
    data = read(args.inventory)
    directories = [path.parent for path in Path(args.artifacts).rglob("results.json")]
    reports = [read(path / "results.json") for path in directories]
    count = validate_gate(data, reports, args.profile, needs)
    coverage_files = []
    for directory in directories:
        report = read(directory / "results.json")
        if not (directory / "junit.xml").is_file():
            raise ValueError(f"Missing JUnit in {directory}")
        junit_cases = list(ET.parse(directory / "junit.xml").getroot().iter("testcase"))
        if len(junit_cases) != len(report["cases"]) or any(
            case.find(kind) is not None for case in junit_cases for kind in ("failure", "error", "skipped")
        ):
            raise ValueError(f"JUnit execution mismatch or unsuccessful case in {directory}")
        if report["group"] != "dashboard" and args.profile == "full":
            if not (directory / "coverage.xml").is_file() or not (directory / "coverage.data").is_file():
                raise ValueError(f"Missing Python coverage in {directory}")
            coverage_files.append(str(directory / "coverage.data"))
        elif report["group"] == "dashboard" and not (directory / "coverage-summary.json").is_file():
            raise ValueError("Missing dashboard coverage summary")
    if args.profile == "full":
        for root in ("dev", "dev-historical-data"):
            paths = list(Path(args.artifacts).rglob(f"terraform/{root}.json"))
            if len(paths) != 1:
                raise ValueError(f"Missing/duplicate Terraform validation report: {root}")
            validation = read(paths[0])
            if validation.get("valid") is not True or validation.get("error_count") != 0:
                raise ValueError(f"Terraform validation report failed: {root}")
    output = REPORTS / "aggregate"
    output.mkdir(parents=True, exist_ok=True)
    if args.profile == "full":
        environment = os.environ.copy()
        environment["COVERAGE_FILE"] = str(output / "combined.coverage")
        subprocess.run([sys.executable, "-m", "coverage", "combine", "--keep", *coverage_files], cwd=ROOT, env=environment, check=True)
        subprocess.run([sys.executable, "-m", "coverage", "xml", "-o", str(output / "coverage.xml")], cwd=ROOT, env=environment, check=True)
    write(output / "gate.json", {"profile": args.profile, "python_cases": count, "status": "passed"})
    summary(f"### CI quality gate: PASS\n{count} unique Python cases accounted for in the {args.profile} profile. All required jobs/reports passed; no unexpected skips.\n")
    return 0


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=("discover", "run", "policy", "dashboard", "gate"))
    parser.add_argument("--profile", choices=("pr", "full"), default="full")
    parser.add_argument("--group", choices=GROUPS)
    parser.add_argument("--inventory", default=str(REPORTS / "inventory.json"))
    parser.add_argument("--artifacts", default=str(REPORTS / "downloads"))
    args = parser.parse_args()
    if args.command == "discover":
        return discover(args.profile, args.inventory)
    if args.command == "run":
        return run_group(args.group, args.inventory)
    if args.command == "policy":
        return check_workflows()
    if args.command == "dashboard":
        return dashboard_report(args.profile)
    return gate(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, ET.ParseError, subprocess.CalledProcessError) as error:
        summary(f"### CI check failed\n{error}\n")
        raise SystemExit(1) from error
