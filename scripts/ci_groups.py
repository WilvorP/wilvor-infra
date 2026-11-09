"""Single ownership map for collected tests. No fixed test-count ceiling."""

GROUPS = ("pipelines", "read-models", "history", "script-safety", "copilot", "infrastructure", "governance")
MATRIX_GROUPS = GROUPS[:5]
PIPELINES = frozenset({
    "active_alerts", "aircraft", "airport_status", "encounter", "metar",
    "projection", "recommendations", "risk", "runway_metadata", "sigmet", "taf",
})
CLI_FILE = "tests/infrastructure/terraform/test_terraform_cli.py"
OPERATOR_EVIDENCE_FILE = "tests/contracts/test_sprint3d_aws_nonproduction.py"
OPERATOR_EVIDENCE_EXCLUSION = "operator-evidence-live-aws"
OPERATOR_EVIDENCE_TESTS = (
    "test_sts_account_identity_is_the_reviewed_account_and_not_the_ai_role",
    "test_ledger_table_schema_matches_the_agent_c_contract",
    "test_aircraft_current_state_schema_matches_the_reviewed_mapping",
    "test_deployed_ai_host_permissions_match_the_agent_c_matrix",
    "test_ai_lambda_reserved_concurrency_remains_zero",
    "test_post_ai_query_route_remains_absent",
    "test_bounded_getitem_uses_operator_credentials_on_the_probe_key",
    "test_unauthorized_live_mappings_are_denied_for_the_ai_role",
    "test_operator_readonly_pass_is_not_production_and_not_an_ai_role_pass",
)
OPERATOR_EVIDENCE_NODEIDS = frozenset(
    f"{OPERATOR_EVIDENCE_FILE}::{name}" for name in OPERATOR_EVIDENCE_TESTS
)
PR_EVENTS = ("opened", "synchronize", "reopened", "labeled", "unlabeled")
PROFILE_EXPRESSION = "${{ github.event_name == 'pull_request' && !contains(github.event.pull_request.labels.*.name, 'ci-full') && 'pr' || 'full' }}"


def profile_for_event(event_name, labels=()):
    """Mirror Actions contains(array, value), which is case-insensitive."""
    return "pr" if event_name == "pull_request" and "ci-full" not in {label.casefold() for label in labels} else "full"


def owner(nodeid):
    normalized = nodeid.replace("\\", "/")
    if normalized in OPERATOR_EVIDENCE_NODEIDS:
        return f"excluded:{OPERATOR_EVIDENCE_EXCLUSION}"
    file = normalized.split("::", 1)[0]
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
