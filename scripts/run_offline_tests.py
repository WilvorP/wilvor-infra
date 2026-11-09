"""Run the explicit offline Python suite with external sockets blocked.

Pass explicit test targets and pytest options, or use ``--`` followed by
pytest options to select the full default suite. No dependency install,
Terraform initialization, AWS authentication or provider calls are performed.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = [
    "tests/unit",
    "tests/contracts",
    "tests/infrastructure/terraform",
    "tests/infrastructure/test_sprint3b_ai_auth_route.py",
]


def main() -> int:
    environment = os.environ.copy()
    # Do not inherit real credentials, endpoint overrides, profiles or live
    # validation opt-ins from a developer shell. Dummy keys permit local SDK
    # construction only; the socket guard enforces the offline boundary.
    for name in list(environment):
        if name.startswith(("AWS_", "WILVOR_AWS_", "WILVOR_RUN_LIVE")) or name == "ANTHROPIC_API_KEY":
            environment.pop(name)
    environment.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_SESSION_TOKEN="testing",
        AWS_EC2_METADATA_DISABLED="true",
        AWS_DEFAULT_REGION="us-west-1",
        AWS_REGION="us-west-1",
        AWS_CONFIG_FILE=os.devnull,
        AWS_SHARED_CREDENTIALS_FILE=os.devnull,
        ENVIRONMENT="test",
        PYTHONPATH=str(REPO_ROOT / "tests" / "offline_bootstrap"),
        PYTHONDONTWRITEBYTECODE="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONUNBUFFERED="1",
        CHECKPOINT_DISABLE="1",
    )
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("WILVOR_TERRAFORM_DIR", None)
    environment.pop("WILVOR_TFPLAN_PATH", None)
    environment.pop("WILVOR_ALLOW_DESTROY", None)
    arguments = sys.argv[1:]
    if arguments[:1] == ["--"]:
        targets = [*DEFAULT_TARGETS, *arguments[1:]]
    elif not arguments or arguments[0].startswith("-"):
        targets = [*DEFAULT_TARGETS, *arguments]
    else:
        targets = arguments
    # Collection never executes fixtures; include plan-dependent cases so CI
    # can account for every explicit Step 2 exclusion by collected node ID.
    exclusions = [] if "--collect-only" in targets else [
        "--ignore=tests/infrastructure/terraform/test_plan_safety.py"
    ]
    return subprocess.call(
        [
            sys.executable, "-B", "-m", "pytest", *targets,
            *exclusions,
            "--strict-markers", "-p", "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        env=environment,
    )


if __name__ == "__main__":
    raise SystemExit(main())
