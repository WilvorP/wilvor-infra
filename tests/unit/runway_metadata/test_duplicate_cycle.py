from cycle_control import CycleDecisionType, decide_cycle


def test_same_cycle_and_hash_is_skipped() -> None:
    decision = decide_cycle(
        current_cycle="2026-07-09",
        current_source_hash="abc",
        incoming_cycle="2026-07-09",
        incoming_source_hash="abc",
    )

    assert decision.decision is CycleDecisionType.DUPLICATE
    assert decision.should_process is False


def test_same_cycle_with_new_hash_is_corrected_package() -> None:
    decision = decide_cycle(
        current_cycle="2026-07-09",
        current_source_hash="abc",
        incoming_cycle="2026-07-09",
        incoming_source_hash="def",
    )

    assert decision.decision is CycleDecisionType.CORRECTED_PACKAGE
    assert decision.should_process is True


def test_force_reload_overrides_duplicate() -> None:
    decision = decide_cycle(
        current_cycle="2026-07-09",
        current_source_hash="abc",
        incoming_cycle="2026-07-09",
        incoming_source_hash="abc",
        force_reload=True,
    )

    assert decision.decision is CycleDecisionType.FORCE_RELOAD
    assert decision.should_process is True
