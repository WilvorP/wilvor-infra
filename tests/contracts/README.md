# Contract tests

**Canonical page:** this page for the `tests/contracts/` index; maturity map on [Tests and maturity](../../docs/ai/tests-and-maturity.md)
**Verified against:** `8abd2d5` on `docs/ws5-ai-copilot`
**Maturity:** CONTRACT, LIVE
**Production authorization:** `MasterRouteExecutionAuthority.DENIED`; a passing contract test is not a production grant

## Purpose

This page indexes the AI Operations Copilot contract suite. It states which files exist and what they assert at the module level. It does not record a current pytest pass count. Specialist, tool, and Master behavior pages own the detailed claims.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Contract test files | 117 `test_*.py` files | named search: `tests/contracts/test_*.py` at `8abd2d5` |
| Current pytest pass count | UNKNOWN | this documentation task did not run `pytest` |
| Live harnesses | LIVE | `scripts/validate_*anthropic_live.py` |
| Current live pass | UNKNOWN | harnesses are opt-in; the suite does not invoke them |
| Terraform | TF absent for an Agent API | named search in [Tests and maturity](../../docs/ai/tests-and-maturity.md) |
| Deployment | UNKNOWN | not verified |
| Production authorization | `DENIED` | `functions/shared/wilvor_ai/master_routing.py` (`MasterRouteExecutionAuthority`) |

This page states contract-suite inventory. Deep behavior lives on `docs/ai/` and `functions/shared/wilvor_ai/README.md`.

## Behavior

Run from the repository root:

```powershell
python -m pytest tests/contracts -q -p no:cacheprovider
```

That command is the suite entry recorded in `functions/shared/wilvor_ai/README.md`. A result is a current pass only when a dated command output is cited. None is cited here.

The examples in `tests/fixtures/ai_copilot_contract_examples.py` use synthetic identifiers and make no claims about live aviation operations.

Six Live Operations region tests in this suite can fail for an unrelated checksum: committed `us_states.geojson` raw bytes contain one CRLF, while `us_states.meta.json` hashes the LF bytes. That is not a decision-contract failure (source: `functions/shared/wilvor_ai/README.md`).

Import-isolation tests parse sibling modules in a fresh interpreter and keep AWS/provider SDKs and specialist runtimes off unintended import paths.

### Inventory

| Group | Files | What they assert |
| --- | --- | --- |
| Phase 0 contracts | `test_ai_copilot_contracts.py`, `test_evidence_provenance.py`, `test_model_contracts.py`, `test_specialist_contracts.py`, `test_tool_schema.py`, `test_tool_result_projection.py` | `V1_AUTHORITY`, evidence, tool envelopes, schemas, projections |
| Live Ops | `test_live_ops_*.py`, `test_anthropic_live_ops_messages_provider.py` | eight-tool catalog, adapter, specialist orchestration, verifier, public export; offline harness tests |
| Historical | `test_historical_*.py` | four-tool catalog, specialist loop, instructions, deterministic verification |
| Decision | `test_decision_*.py`, `test_persisted_airport_evidence.py`, `test_anthropic_decision_messages_provider.py` | four-tool catalog, specialist, verifier, renderer, closure, persisted airport evidence |
| Providers | `test_anthropic_messages_provider.py`, `test_bedrock_converse_provider.py`, `test_provider_import_isolation.py` | offline injected-client adapters; no live AWS or Anthropic call |
| Master MA1–MA4 | `test_master_contracts.py`, `test_master_routing.py`, `test_master_orchestration.py`, `test_master_evidence_*.py`, `test_ma4_*.py` | classification, denied routing, synthetic workers, admission/corroboration/comparability |
| Master MA5–MA7 | remaining `test_master_*.py`, `test_ma7_*.py`, `test_sprint3a_ma7_*.py`, `test_master_offline_integration.py` | nonfactual response, blocked preflight, bindings, readiness, provenance, safety matrix and Sprint 3A regressions |

`test_live_ops_anthropic_live_harness.py` tests the harness module offline. Live actions stay behind `WILVOR_RUN_LIVE_LIVE_OPS_ANTHROPIC` and are not invoked by the test suite (source: `scripts/validate_live_ops_specialist_anthropic_live.py`).

`test_ma7_master_safety_matrix.py` still says "MA5 and MA6 modules are absent on this branch". Those modules exist at `8abd2d5`. This workstream does not edit that test file.

Sprint 3A regression names that lock identifier, readiness, and verified-zero distinctions are listed on [Tests and maturity](../../docs/ai/tests-and-maturity.md).

### Live harnesses (not a pass result)

| Script | Opt-in environment |
| --- | --- |
| `scripts/validate_live_ops_specialist_anthropic_live.py` | `WILVOR_RUN_LIVE_LIVE_OPS_ANTHROPIC` |
| `scripts/validate_historical_specialist_anthropic_live.py` | `WILVOR_RUN_LIVE_ANTHROPIC` |
| `scripts/validate_decision_specialist_anthropic_live.py` | `WILVOR_RUN_LIVE_DECISION_ANTHROPIC` |

Related UNIT tests of harness dry-run logic live under `tests/unit/scripts/`. Those files are UNIT, not CONTRACT.

## Known limitations

- CONTRACT is not UNIT, INFRA, INT, LIVE pass, DEPLOYED, or production authorization.
- No Agent API, dashboard `/ai`, or Terraform AI wiring was found at this commit. Search scopes are on [Tests and maturity](../../docs/ai/tests-and-maturity.md).
- Stored live JSON artifacts cited in the package README are historical evidence and are not in this worktree (`test-results/` is gitignored).
- Dated pytest counts in `functions/shared/wilvor_ai/SPRINT3A_MA7_SECURITY_REVIEW.md` are historical at `7073b3a`.

## Historical record

No superseded statement is stored on this page. This index did not exist before the documentation workstream.

## Sources

- Source: `tests/contracts/test_*.py` (117 files at `8abd2d5`)
- Source: `functions/shared/wilvor_ai/README.md`
- Source: `scripts/validate_live_ops_specialist_anthropic_live.py`
- Mapping: `docs/ai/tests-and-maturity.md`
