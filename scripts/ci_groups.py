"""Single ownership map for collected tests. No fixed test-count ceiling."""

GROUPS = ("pipelines", "read-models", "history", "script-safety", "copilot", "infrastructure", "governance")
MATRIX_GROUPS = GROUPS[:5]
PIPELINES = frozenset({
    "active_alerts", "aircraft", "airport_status", "encounter", "metar",
    "projection", "recommendations", "risk", "runway_metadata", "sigmet", "taf",
})
CLI_FILE = "tests/infrastructure/terraform/test_terraform_cli.py"


def owner(nodeid):
    file = nodeid.split("::", 1)[0].replace("\\", "/")
    if file.startswith("tests/contracts/"):
        return "copilot"
    if file.startswith("tests/unit/"):
        domain = file.split("/")[2]
        if domain in PIPELINES:
            return "pipelines"
        if domain in {"shared", "operational_api"}:
            return "read-models"
        if domain in {"historical_facts", "historical_query"}:
            return "history"
        if domain == "scripts":
            return "script-safety"
        if domain == "ci":
            return "governance"
    if file == "tests/infrastructure/terraform/test_plan_safety.py":
        return "excluded:supplied-plan-step2"
    if file.startswith("tests/infrastructure/aws/"):
        return "excluded:deployed-aws-step2"
    if file.startswith("tests/integration/"):
        return "excluded:deployed-integration-step2"
    if file.startswith("tests/infrastructure/terraform/") or file == "tests/infrastructure/test_sprint3b_ai_auth_route.py":
        return "infrastructure"
    raise ValueError(f"Unclassified collected test: {nodeid}; register an explicit owner")


def selected(group, nodeid, profile):
    if profile not in {"pr", "full"}:
        raise ValueError(f"Unknown CI profile: {profile}")
    if profile == "full":
        return True
    return group in {"governance", "copilot", "script-safety", "infrastructure"} and not nodeid.startswith(CLI_FILE + "::")


def inventory(nodeids, profile):
    if len(nodeids) != len(set(nodeids)):
        raise ValueError("Duplicate collected node IDs")
    groups = {group: [] for group in GROUPS}
    excluded = {}
    deferred = []
    for nodeid in sorted(nodeids):
        group = owner(nodeid)
        if group.startswith("excluded:"):
            excluded.setdefault(group.removeprefix("excluded:"), []).append(nodeid)
        else:
            groups[group].append(nodeid)
            if not selected(group, nodeid, profile):
                deferred.append(nodeid)
    return {"schema": 1, "profile": profile, "groups": groups, "excluded": excluded, "profile_deferred": deferred}


def expected(data, group):
    return [nodeid for nodeid in data["groups"][group] if selected(group, nodeid, data["profile"])]
