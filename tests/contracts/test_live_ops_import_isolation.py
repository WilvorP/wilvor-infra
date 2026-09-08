"""Import isolation tests for Phase 1E Live Operations adapters."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

from tests.fixtures import live_ops_domain_fixtures as fixtures
from wilvor_ai.live_ops import LiveOpsCall


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
LIVE_OPS = SHARED_DIR / "wilvor_ai" / "live_ops.py"
LIVE_OPS_MAPPING = SHARED_DIR / "wilvor_ai" / "live_ops_mapping.py"


def _run_isolated(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_import_wilvor_ai_is_isolated_from_operational_and_models():
    script = """
import sys
import wilvor_ai
assert 'wilvor_operational' not in sys.modules
assert 'wilvor_operational.geospatial' not in sys.modules
assert 'wilvor_operational.geometry' not in sys.modules
assert not any(
    module == 'shapely' or module.startswith('shapely.')
    for module in sys.modules
)
assert not any(module.startswith('functions.ai_copilot') for module in sys.modules)
assert 'boto3' not in sys.modules
assert 'openai' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'langgraph' not in sys.modules
assert 'wilvor_ai.live_ops' not in sys.modules
assert hasattr(wilvor_ai, 'ToolResult')
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr


def test_import_live_ops_is_shapely_and_geospatial_free():
    script = """
import sys
import wilvor_ai.live_ops
assert 'wilvor_operational.query' in sys.modules
assert 'wilvor_operational.geospatial' not in sys.modules
assert 'wilvor_operational.geometry' not in sys.modules
assert not any(
    module == 'shapely' or module.startswith('shapely.')
    for module in sys.modules
)
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr


def test_non_region_hazard_and_impact_tools_stay_shapely_free():
    script = f"""
import sys
from wilvor_ai.live_ops import LiveOpsCall, search_current_hazards, search_current_impacts
from tests.fixtures.live_ops_domain_fixtures import tables, FlexibleTable, hazard, NOW, TOOL_CALL_ID

h1 = hazard('H1')
call = LiveOpsCall(tables=tables(hazards=FlexibleTable(records={{'H1': h1}})), now_epoch=NOW, tool_call_id=TOOL_CALL_ID)
search_current_hazards(call, hazard_ids=['H1'])
search_current_impacts(call, hazard_ids=['H1'])
assert 'wilvor_operational.geospatial' not in sys.modules
assert 'wilvor_operational.geometry' not in sys.modules
assert not any(
    module == 'shapely' or module.startswith('shapely.')
    for module in sys.modules
)
"""
    env = os.environ.copy()
    pythonpath = os.pathsep.join((str(REPO_ROOT), str(SHARED_DIR), env.get("PYTHONPATH", "")))
    env["PYTHONPATH"] = pythonpath
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_live_ops_modules_have_no_clock_boto3_or_aws_env_lookup():
    for path in (LIVE_OPS, LIVE_OPS_MAPPING):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert "boto3" not in imported
        assert "botocore" not in imported
        assert "time" not in imported
        assert "shapely" not in imported
        assert "wilvor_operational.geospatial" not in imported
        assert "wilvor_operational.geometry" not in imported
        assert "time.time" not in source
        assert "boto3.resource" not in source
        assert "boto3.client" not in source
        assert "os.environ" not in source
        assert "AWS_" not in source


def test_region_execution_may_load_geospatial():
    from wilvor_ai.live_ops import search_current_hazards

    search_current_hazards(
        LiveOpsCall(
            tables=fixtures.california_tables(),
            now_epoch=fixtures.NOW,
            tool_call_id=fixtures.TOOL_CALL_ID,
        ),
        region="California",
        product_type="SIGMET",
    )
    assert "wilvor_operational.geospatial" in sys.modules
