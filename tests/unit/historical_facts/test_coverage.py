"""Pure collection evaluability, horizons, and probe confirmation."""

from __future__ import annotations

import ast
import gzip
import importlib
import inspect
import json
from datetime import datetime, timedelta, timezone

import pytest

from wilvor_historical.coverage import (
    DATA_FRESHNESS_THRESHOLD_SECONDS,
    DEFAULT_COLLECTION_EVALUATION_POLICY,
    ENCOUNTER_HORIZON_SECONDS,
    HAZARD_GEOMETRY_HORIZON_SECONDS,
    HAZARD_VERSION_HORIZON_SECONDS,
    RISK_HORIZON_SECONDS,
    S3_DESTINATION_FAILURE,
    CollectionEvaluationPolicy,
    FirehoseDeliveryMetrics,
    bound_gap_dedup_id,
    bound_gap_from_incident,
    collection_control_prefixes,
    confirm_probes_in_jsonl,
    datasets_for_stream,
    domain_3b_canonical_interval,
    domain_3b_gap_record,
    domain_3b_impairments_from_metrics,
    evaluate_collection_window,
    parse_control_jsonl,
    pre_firehose_bound_seconds,
    probe_dedup_id,
    required_streams_for,
    utc_date_prefixes_for_lookback,
)
from wilvor_historical.coverage_contracts import (
    COLLECTION_CONTROL_DATASET,
    CONTROL_SCHEMA_VERSION,
    STAGING_STATE,
    CollectionActivationRecord,
    CollectionDeactivationRecord,
    CollectionGapRecord,
    CollectionGapResolutionRecord,
    CollectionProbeRecord,
    CoverageIntervalRecord,
    CoverageStream,
    Evaluability,
    GapDomain,
    HistoricalCoverageError,
    RecoveryState,
    UncertaintyClass,
    UnboundCollectionIncident,
    ControlRecordType,
)
from wilvor_historical.time import canonicalize_utc_z


UTC = timezone.utc
POLICY = DEFAULT_COLLECTION_EVALUATION_POLICY
EPOCH = "epoch-1"
START = "2026-07-18T12:00:00Z"
END = "2026-07-18T12:15:00Z"


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _z(value: datetime) -> str:
    return canonicalize_utc_z(value)


def _activation(enabled_at: str = "2026-07-18T00:00:00Z", *, epoch: str = EPOCH):
    return CollectionActivationRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_ACTIVATION,
        collection_epoch_id=epoch,
        enabled_at_utc=enabled_at,
        created_at_utc=enabled_at,
        dedup_id=f"activation|{epoch}|{enabled_at}",
    )


def _deactivation(deactivated_at: str, *, epoch: str = EPOCH):
    return CollectionDeactivationRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_DEACTIVATION,
        collection_epoch_id=epoch,
        deactivated_at_utc=deactivated_at,
        created_at_utc=deactivated_at,
        dedup_id=f"deactivation|{epoch}|{deactivated_at}",
    )


def _coverage(
    stream: CoverageStream,
    start: str = START,
    end: str = END,
    *,
    epoch: str = EPOCH,
):
    return CoverageIntervalRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COVERAGE_INTERVAL,
        collection_epoch_id=epoch,
        stream=stream,
        interval_start_utc=start,
        interval_end_utc=end,
        created_at_utc=end,
        dedup_id=f"coverage|{stream.value}|{start}",
    )


def _gap(
    datasets: tuple[str, ...],
    *,
    start: str = START,
    end: str = END,
    recovery: RecoveryState = RecoveryState.OPEN,
    epoch: str = EPOCH,
    dedup_id: str = "gap-1",
):
    return CollectionGapRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_GAP,
        collection_epoch_id=epoch,
        affected_datasets=datasets,
        gap_domain=GapDomain.DOMAIN_1,
        reason="PRODUCER_PUT_EVENTS_FAILURE",
        uncertainty_class=UncertaintyClass.KNOWN_MISSING,
        recovery_state=recovery,
        detected_at_utc=start,
        interval_start_utc=start,
        interval_end_utc=end,
        created_at_utc=start,
        dedup_id=dedup_id,
    )


def _resolution(gap_dedup_id: str, *, state=RecoveryState.RESOLVED_PROVEN, epoch: str = EPOCH):
    return CollectionGapResolutionRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_GAP_RESOLUTION,
        collection_epoch_id=epoch,
        gap_dedup_id=gap_dedup_id,
        recovery_state=state,
        created_at_utc=END,
        dedup_id=f"resolution|{epoch}|{gap_dedup_id}",
    )


def _probe(
    stream: CoverageStream,
    probe_id: str = "probe-1",
    *,
    epoch: str = EPOCH,
    start: str = START,
    end: str = END,
):
    observed = _utc(start)
    return CollectionProbeRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_PROBE,
        collection_epoch_id=epoch,
        stream=stream,
        probe_id=probe_id,
        interval_start_utc=start,
        interval_end_utc=end,
        observed_at_utc=start,
        dataset=COLLECTION_CONTROL_DATASET,
        event_year=f"{observed.year:04d}",
        event_month=f"{observed.month:02d}",
        event_day=f"{observed.day:02d}",
        dedup_id=probe_dedup_id(stream, start, probe_id),
    )


def _incident(
    datasets: tuple[str, ...] = ("encounter",),
    *,
    start: str = START,
    end: str = "2026-07-18T12:00:01Z",
    dedup_id: str = "domain1|encounter|enc-1|2026-07-18T12:00:00Z",
    reason: str = "PRODUCER_PUT_EVENTS_FAILURE",
    domain: GapDomain = GapDomain.DOMAIN_1,
):
    return UnboundCollectionIncident(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.UNBOUND_COLLECTION_INCIDENT,
        staging_state=STAGING_STATE,
        affected_datasets=datasets,
        gap_domain=domain,
        reason=reason,
        uncertainty_class=UncertaintyClass.KNOWN_MISSING,
        detected_at_utc=start,
        interval_start_utc=start,
        interval_end_utc=end,
        created_at_utc=start,
        dedup_id=dedup_id,
        identity="enc-1",
        source_subsystem="encounter",
        producer_source="wilvor.encounter",
        producer_detail_type="encounter.updated",
    )


def _evaluate(*, as_of: str, datasets=("encounter",), **kwargs):
    defaults = {
        "datasets": datasets,
        "start_utc": START,
        "end_utc": END,
        "as_of_utc": as_of,
        "activations": [_activation()],
        "deactivations": (),
        "coverage_intervals": [_coverage(CoverageStream.FACTS)],
        "gap_records": (),
        "resolutions": (),
        "policy": POLICY,
        "collection_epoch_id": EPOCH,
    }
    defaults.update(kwargs)
    return evaluate_collection_window(**defaults)


def test_policy_horizons_are_locked_constants():
    assert POLICY.horizon_for("encounter") == ENCOUNTER_HORIZON_SECONDS == 173760
    assert POLICY.horizon_for("risk") == RISK_HORIZON_SECONDS == 173730
    assert (
        POLICY.horizon_for("hazard_version")
        == HAZARD_VERSION_HORIZON_SECONDS
        == 173760
    )
    assert (
        POLICY.horizon_for("hazard_geometry")
        == HAZARD_GEOMETRY_HORIZON_SECONDS
        == 87360
    )
    assert POLICY.horizon_for("encounter") == 60 + 86400 + 86400 + 900
    assert POLICY.horizon_for("risk") == 30 + 86400 + 86400 + 900
    assert POLICY.horizon_for("hazard_version") == 60 + 86400 + 86400 + 900
    assert POLICY.horizon_for("hazard_geometry") == 60 + 86400 + 900
    assert POLICY.probe_lookback_seconds(CoverageStream.FACTS) == 173760
    assert POLICY.probe_lookback_seconds(CoverageStream.GEOMETRY) == 87360
    assert POLICY.probe_confirm_delay_seconds() == 1800


def test_mixed_query_uses_max_horizon():
    mixed = POLICY.required_horizon_seconds(("risk", "hazard_geometry"))
    assert mixed == max(173730, 87360) == 173730
    both = POLICY.required_horizon_seconds(
        ("encounter", "risk", "hazard_version", "hazard_geometry")
    )
    assert both == 173760


def test_dataset_stream_mapping():
    assert required_streams_for(("encounter",)) == (CoverageStream.FACTS,)
    assert required_streams_for(("risk", "encounter")) == (CoverageStream.FACTS,)
    assert required_streams_for(("hazard_geometry",)) == (CoverageStream.GEOMETRY,)
    assert required_streams_for(("hazard_version", "hazard_geometry")) == (
        CoverageStream.FACTS,
        CoverageStream.GEOMETRY,
    )


@pytest.mark.parametrize(
    "datasets,horizon",
    [
        (("encounter",), 173760),
        (("risk",), 173730),
        (("hazard_version",), 173760),
        (("hazard_geometry",), 87360),
        (("encounter", "hazard_geometry"), 173760),
        (("risk", "hazard_geometry"), 173730),
    ],
)
def test_horizon_minus_one_equals_not_yet_evaluable(datasets, horizon):
    end = _utc(END)
    as_of = _z(end + timedelta(seconds=horizon - 1))
    coverage = [_coverage(CoverageStream.FACTS)]
    if "hazard_geometry" in datasets:
        coverage.append(_coverage(CoverageStream.GEOMETRY))
    result = _evaluate(as_of=as_of, datasets=datasets, coverage_intervals=coverage)
    assert result.evaluability is Evaluability.NOT_YET_EVALUABLE
    assert result.required_horizon_seconds == horizon


@pytest.mark.parametrize(
    "datasets,horizon",
    [
        (("encounter",), 173760),
        (("risk",), 173730),
        (("hazard_version",), 173760),
        (("hazard_geometry",), 87360),
        (("encounter", "hazard_geometry"), 173760),
        (("risk", "hazard_geometry"), 173730),
    ],
)
def test_horizon_and_plus_one_are_closed(datasets, horizon):
    end = _utc(END)
    coverage = [_coverage(CoverageStream.FACTS)]
    if "hazard_geometry" in datasets:
        coverage.append(_coverage(CoverageStream.GEOMETRY))
    for extra in (0, 1):
        as_of = _z(end + timedelta(seconds=horizon + extra))
        result = _evaluate(
            as_of=as_of,
            datasets=datasets,
            coverage_intervals=coverage,
        )
        assert result.evaluability is Evaluability.EVALUABLE
        assert result.required_horizon_seconds == horizon


def test_future_window_is_not_yet_evaluable():
    result = _evaluate(as_of="2026-07-18T12:14:59Z")
    assert result.evaluability is Evaluability.NOT_YET_EVALUABLE
    assert result.reason == "window_ends_after_as_of"


def test_naive_and_non_utc_and_inverted_window_raise():
    with pytest.raises(HistoricalCoverageError):
        _evaluate(as_of="2026-07-20T12:15:00")
    with pytest.raises(HistoricalCoverageError):
        _evaluate(as_of="2026-07-20T12:15:00+01:00")
    with pytest.raises(HistoricalCoverageError):
        evaluate_collection_window(
            datasets=("encounter",),
            start_utc=END,
            end_utc=START,
            as_of_utc="2026-07-20T12:15:00Z",
            activations=[_activation()],
            collection_epoch_id=EPOCH,
        )


def test_foreign_epoch_records_are_ignored():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    result = _evaluate(
        as_of=as_of,
        activations=[_activation(epoch="other")],
        coverage_intervals=[_coverage(CoverageStream.FACTS, epoch="other")],
    )
    assert result.evaluability is Evaluability.NOT_ACTIVE


def test_old_real_epoch_gap_is_ignored_after_store_reset():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    result = _evaluate(
        as_of=as_of,
        coverage_intervals=[_coverage(CoverageStream.FACTS)],
        gap_records=[_gap(("encounter",), epoch="old-epoch-before-reset")],
    )
    assert result.evaluability is Evaluability.EVALUABLE


def test_reserved_epoch_tokens_are_rejected():
    with pytest.raises(HistoricalCoverageError, match="staging token"):
        _gap(("encounter",), epoch="unbound")
    with pytest.raises(HistoricalCoverageError, match="staging token"):
        _evaluate(
            as_of=_z(_utc(END) + timedelta(seconds=173760)),
            collection_epoch_id="unbound",
        )
    with pytest.raises(HistoricalCoverageError, match="staging token"):
        bound_gap_dedup_id("unbound", "incident-1")
    incident = _incident()
    payload = incident.to_dict()
    assert "collection_epoch_id" not in payload
    with pytest.raises(HistoricalCoverageError, match="must not carry an epoch"):
        payload["collection_epoch_id"] = "unbound"
        from wilvor_historical.coverage import unbound_incident_from_dict

        unbound_incident_from_dict(payload)


def test_staging_incident_alone_is_never_evaluable():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    with_coverage = _evaluate(
        as_of=as_of,
        unbound_incidents=[_incident()],
    )
    assert with_coverage.evaluability is Evaluability.GAP_OR_UNCERTAIN
    assert with_coverage.reason == "unbound_incident_overlaps_window"
    without_coverage = _evaluate(
        as_of=as_of,
        coverage_intervals=(),
        unbound_incidents=[_incident()],
    )
    assert without_coverage.evaluability is Evaluability.GAP_OR_UNCERTAIN


def test_bound_gap_from_incident_blocks_evaluable():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    incident = _incident()
    bound = bound_gap_from_incident(incident, collection_epoch_id=EPOCH)
    assert bound.collection_epoch_id == EPOCH
    assert bound.dedup_id == bound_gap_dedup_id(EPOCH, incident.dedup_id)
    assert bound.created_at_utc == incident.created_at_utc
    assert f"incident|{incident.dedup_id}" in bound.evidence_refs
    result = _evaluate(as_of=as_of, gap_records=[bound])
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN
    assert result.reason == "unresolved_gap_overlaps_window"
    again = bound_gap_from_incident(incident, collection_epoch_id=EPOCH)
    assert again.dedup_id == bound.dedup_id
    assert again.to_dict() == bound.to_dict()


def test_window_before_activation_is_not_active():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    result = _evaluate(
        as_of=as_of,
        activations=[_activation("2026-07-18T18:00:00Z")],
    )
    assert result.evaluability is Evaluability.NOT_ACTIVE


def test_window_entirely_inside_closed_period_is_not_active():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    result = _evaluate(
        as_of=as_of,
        activations=[_activation("2026-07-18T00:00:00Z")],
        deactivations=[_deactivation("2026-07-18T11:00:00Z")],
    )
    assert result.evaluability is Evaluability.NOT_ACTIVE


def test_mixed_active_and_intentional_inactive_is_not_active():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    result = evaluate_collection_window(
        datasets=("encounter",),
        start_utc="2026-07-18T11:45:00Z",
        end_utc="2026-07-18T12:15:00Z",
        as_of_utc=as_of,
        activations=[_activation("2026-07-18T12:00:00Z")],
        deactivations=(),
        coverage_intervals=[
            _coverage(CoverageStream.FACTS, "2026-07-18T12:00:00Z", END)
        ],
        collection_epoch_id=EPOCH,
    )
    assert result.evaluability is Evaluability.NOT_ACTIVE
    assert result.reason == "window_mixes_active_and_intentionally_inactive"


def test_mixed_window_with_open_gap_on_active_slice_is_uncertain():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    result = evaluate_collection_window(
        datasets=("encounter",),
        start_utc="2026-07-18T11:45:00Z",
        end_utc="2026-07-18T12:15:00Z",
        as_of_utc=as_of,
        activations=[_activation("2026-07-18T12:00:00Z")],
        coverage_intervals=[
            _coverage(CoverageStream.FACTS, "2026-07-18T12:00:00Z", END)
        ],
        gap_records=[_gap(("encounter",), start="2026-07-18T12:00:00Z")],
        collection_epoch_id=EPOCH,
    )
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN


def test_missing_deactivation_and_missing_coverage_fails_closed():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    result = _evaluate(
        as_of=as_of,
        coverage_intervals=(),
    )
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN
    assert "missing_coverage_interval" in result.reason


def test_facts_healthy_geometry_gapped_encounter_is_evaluable():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    result = _evaluate(
        as_of=as_of,
        datasets=("encounter",),
        coverage_intervals=[_coverage(CoverageStream.FACTS)],
        gap_records=[_gap(("hazard_geometry",))],
    )
    assert result.evaluability is Evaluability.EVALUABLE
    assert result.required_streams == ("facts",)


def test_same_state_geometry_only_is_uncertain():
    as_of = _z(_utc(END) + timedelta(seconds=87360))
    result = _evaluate(
        as_of=as_of,
        datasets=("hazard_geometry",),
        coverage_intervals=[_coverage(CoverageStream.FACTS)],
        gap_records=[_gap(("hazard_geometry",))],
    )
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN


def test_mixed_hazard_version_and_geometry_requires_both_streams():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    result = _evaluate(
        as_of=as_of,
        datasets=("hazard_version", "hazard_geometry"),
        coverage_intervals=[_coverage(CoverageStream.FACTS)],
    )
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN
    assert result.required_streams == ("facts", "geometry")


def test_geometry_healthy_facts_gapped_geometry_only_is_evaluable():
    as_of = _z(_utc(END) + timedelta(seconds=87360))
    result = _evaluate(
        as_of=as_of,
        datasets=("hazard_geometry",),
        coverage_intervals=[_coverage(CoverageStream.GEOMETRY)],
        gap_records=[_gap(("encounter",))],
    )
    assert result.evaluability is Evaluability.EVALUABLE
    assert result.required_streams == ("geometry",)


def test_encounter_and_risk_share_one_facts_stream_requirement():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    result = _evaluate(
        as_of=as_of,
        datasets=("encounter", "risk"),
        coverage_intervals=[_coverage(CoverageStream.FACTS)],
    )
    assert result.evaluability is Evaluability.EVALUABLE
    assert result.required_streams == ("facts",)


def test_unresolved_and_replayed_gaps_block_resolved_does_not():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    open_gap = _evaluate(
        as_of=as_of,
        gap_records=[_gap(("encounter",), recovery=RecoveryState.OPEN)],
    )
    replayed = _evaluate(
        as_of=as_of,
        gap_records=[
            _gap(("encounter",), recovery=RecoveryState.REPLAY_PERSISTED)
        ],
    )
    unproven = _evaluate(
        as_of=as_of,
        gap_records=[
            _gap(
                ("encounter",),
                recovery=RecoveryState.UNABLE_TO_PROVE_COMPLETE,
            )
        ],
    )
    resolved = _evaluate(
        as_of=as_of,
        gap_records=[_gap(("encounter",), dedup_id="gap-resolved")],
        resolutions=[_resolution("gap-resolved")],
    )
    outside = _evaluate(
        as_of=as_of,
        gap_records=[
            _gap(
                ("encounter",),
                start="2026-07-18T10:00:00Z",
                end="2026-07-18T10:15:00Z",
            )
        ],
    )
    assert open_gap.evaluability is Evaluability.GAP_OR_UNCERTAIN
    assert replayed.evaluability is Evaluability.GAP_OR_UNCERTAIN
    assert unproven.evaluability is Evaluability.GAP_OR_UNCERTAIN
    assert resolved.evaluability is Evaluability.EVALUABLE
    assert outside.evaluability is Evaluability.EVALUABLE


def test_resolution_must_reference_exact_gap_dedup():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    result = _evaluate(
        as_of=as_of,
        gap_records=[_gap(("encounter",), dedup_id="gap-a")],
        resolutions=[_resolution("gap-b")],
    )
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN


def test_foreign_epoch_resolution_is_ignored():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    result = _evaluate(
        as_of=as_of,
        gap_records=[_gap(("encounter",), dedup_id="gap-a")],
        resolutions=[_resolution("gap-a", epoch="other-epoch")],
    )
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN


def test_missing_probe_is_evaluable_only_after_matching_resolution():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    gap = CollectionGapRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_GAP,
        collection_epoch_id=EPOCH,
        affected_datasets=("encounter", "risk", "hazard_version"),
        gap_domain=GapDomain.CONTROL,
        reason="MISSING_PROBE",
        uncertainty_class=UncertaintyClass.TRANSPORT_OR_CONTROL_OUTAGE,
        recovery_state=RecoveryState.OPEN,
        detected_at_utc=START,
        interval_start_utc=START,
        interval_end_utc=END,
        created_at_utc=START,
        dedup_id="missing-probe|facts|2026-07-18T12:00:00Z",
        evidence_refs=("probe-1",),
    )
    blocked = _evaluate(as_of=as_of, gap_records=[gap])
    resolved = _evaluate(
        as_of=as_of,
        gap_records=[gap],
        resolutions=[_resolution(gap.dedup_id)],
    )
    assert blocked.evaluability is Evaluability.GAP_OR_UNCERTAIN
    assert resolved.evaluability is Evaluability.EVALUABLE


def test_late_probe_without_closed_horizon_is_not_evaluable():
    as_of = _z(_utc(END) + timedelta(seconds=173759))
    result = _evaluate(as_of=as_of)
    assert result.evaluability is Evaluability.NOT_YET_EVALUABLE


def test_control_records_round_trip_without_decimal():
    activation = _activation()
    gap = _gap(("encounter", "risk"))
    payload = gap.to_dict()
    assert payload["affected_datasets"] == ["encounter", "risk"]
    assert payload["control_schema_version"] == CONTROL_SCHEMA_VERSION
    assert json.loads(json.dumps(payload)) == payload
    assert activation.to_dict()["record_type"] == "COLLECTION_ACTIVATION"


def test_probe_is_excluded_from_operational_datasets():
    with pytest.raises(HistoricalCoverageError):
        CollectionEvaluationPolicy().horizon_for(COLLECTION_CONTROL_DATASET)
    probe = _probe(CoverageStream.FACTS)
    assert probe.dataset == COLLECTION_CONTROL_DATASET
    assert probe.dataset not in ("encounter", "risk", "hazard_version", "hazard_geometry")


def test_utc_lookback_emits_up_to_four_facts_prefixes():
    scan_as_of = datetime(2026, 7, 21, 0, 5, tzinfo=UTC)
    prefixes = utc_date_prefixes_for_lookback(scan_as_of, 173760)
    assert prefixes == [
        "year=2026/month=07/day=18",
        "year=2026/month=07/day=19",
        "year=2026/month=07/day=20",
        "year=2026/month=07/day=21",
    ]
    assert len(prefixes) == 4
    geometry = utc_date_prefixes_for_lookback(scan_as_of, 87360)
    assert geometry == [
        "year=2026/month=07/day=19",
        "year=2026/month=07/day=20",
        "year=2026/month=07/day=21",
    ]
    assert len(geometry) == 3
    assert "today" not in "".join(prefixes)
    assert "yesterday" not in "".join(prefixes)
    control = collection_control_prefixes(scan_as_of, 173760)
    assert control[0].startswith("dataset=_collection_control/")


def test_exact_probe_in_gzip_jsonl_is_confirmation_not_object_count():
    expected = _probe(CoverageStream.FACTS, "probe-old")
    foreign = _probe(CoverageStream.FACTS, "probe-old", epoch="other")
    other = _probe(CoverageStream.GEOMETRY, "probe-old")
    lines = [
        json.dumps(expected.to_dict()),
        json.dumps(foreign.to_dict()),
        json.dumps(other.to_dict()),
        json.dumps(_probe(CoverageStream.FACTS, "probe-2").to_dict()),
    ]
    blob = gzip.compress(("\n".join(lines) + "\n").encode("utf-8"))
    text = gzip.decompress(blob).decode("utf-8")
    found = confirm_probes_in_jsonl(text, [expected])
    assert found == {
        ("probe-old", "facts", START, EPOCH),
    }
    assert "dataset=_collection_control/" not in found


def test_malformed_jsonl_is_not_confirmation():
    with pytest.raises(HistoricalCoverageError, match="malformed"):
        parse_control_jsonl('{"probe_id":"x"}\nnot-json\n')


def test_lexicographically_earlier_object_still_discoverable():
    expected = _probe(CoverageStream.FACTS, "late-probe")
    earlier_key = "dataset=_collection_control/year=2026/month=07/day=18/aaa.json.gz"
    later_key = "dataset=_collection_control/year=2026/month=07/day=18/zzz.json.gz"
    objects = {
        later_key: json.dumps(_probe(CoverageStream.FACTS, "other").to_dict()),
        earlier_key: json.dumps(expected.to_dict()),
    }
    last_seen = later_key
    found: set[tuple[str, str, str, str]] = set()
    for _key, text in objects.items():
        found |= confirm_probes_in_jsonl(text, [expected])
    assert last_seen > earlier_key
    assert found == {("late-probe", "facts", START, EPOCH)}


def test_coverage_module_has_no_aws_or_clock_or_phase1_imports():
    for module_name in (
        "wilvor_historical.coverage",
        "wilvor_historical.coverage_contracts",
    ):
        module = importlib.import_module(module_name)
        imported = {
            alias
            for statement in ast.parse(inspect.getsource(module)).body
            if isinstance(statement, (ast.Import, ast.ImportFrom))
            for alias in (
                [name.name.split(".", 1)[0] for name in statement.names]
                if isinstance(statement, ast.Import)
                else [statement.module.split(".", 1)[0]]
                if statement.module
                else []
            )
        }
        assert "boto3" not in imported
        assert "wilvor_ai" not in imported
        assert "wilvor_operational" not in imported
        source = inspect.getsource(module)
        assert "time.time(" not in source
        assert "datetime.now(" not in source
        assert "langgraph" not in source


def test_data_freshness_threshold_matches_approved_alarm():
    assert DATA_FRESHNESS_THRESHOLD_SECONDS == 1800
    assert pre_firehose_bound_seconds(CoverageStream.FACTS) == 60 + 86400
    assert pre_firehose_bound_seconds(CoverageStream.GEOMETRY) == 60
    assert datasets_for_stream(CoverageStream.FACTS) == (
        "encounter",
        "risk",
        "hazard_version",
    )
    assert datasets_for_stream(CoverageStream.GEOMETRY) == ("hazard_geometry",)


def test_domain_3b_canonical_interval_expands_backward_from_transport_time():
    metric_time = datetime(2026, 7, 18, 12, 22, tzinfo=UTC)
    freshness = 1801.0
    facts_start, facts_end = domain_3b_canonical_interval(
        stream=CoverageStream.FACTS,
        metric_time=metric_time,
        freshness_seconds=freshness,
    )
    geometry_start, geometry_end = domain_3b_canonical_interval(
        stream=CoverageStream.GEOMETRY,
        metric_time=metric_time,
        freshness_seconds=freshness,
    )
    oldest_facts = metric_time - timedelta(seconds=1801 + 60 + 86400)
    oldest_geometry = metric_time - timedelta(seconds=1801 + 60)
    assert facts_start <= oldest_facts
    assert facts_end >= metric_time
    assert facts_start.minute % 15 == 0
    assert facts_start.second == 0
    assert facts_end.minute % 15 == 0
    assert (facts_end - facts_start).total_seconds() >= 900
    assert geometry_start > facts_start
    assert geometry_start <= oldest_geometry
    assert geometry_end == facts_end
    assert facts_start < datetime(2026, 7, 18, 12, 22, tzinfo=UTC)


def test_freshness_above_threshold_creates_potential_domain_3b_gap():
    metric_time = datetime(2026, 7, 18, 12, 15, tzinfo=UTC)
    impairments = domain_3b_impairments_from_metrics(
        FirehoseDeliveryMetrics(
            stream=CoverageStream.FACTS,
            freshness=((metric_time, 1801.0),),
        )
    )
    assert len(impairments) == 1
    gap = domain_3b_gap_record(impairment=impairments[0], collection_epoch_id=EPOCH)
    assert gap.gap_domain is GapDomain.DOMAIN_3B
    assert gap.uncertainty_class is UncertaintyClass.POTENTIAL_GAP
    assert gap.reason == S3_DESTINATION_FAILURE
    assert gap.recovery_state is RecoveryState.OPEN
    assert gap.collection_epoch_id == EPOCH
    assert gap.affected_datasets == ("encounter", "risk", "hazard_version")
    geometry = domain_3b_gap_record(
        impairment=domain_3b_impairments_from_metrics(
            FirehoseDeliveryMetrics(
                stream=CoverageStream.GEOMETRY,
                freshness=((metric_time, 1801.0),),
            )
        )[0],
        collection_epoch_id=EPOCH,
    )
    assert geometry.affected_datasets == ("hazard_geometry",)


def test_incoming_records_alone_do_not_create_domain_3b():
    metric_time = datetime(2026, 7, 18, 12, 15, tzinfo=UTC)
    none = domain_3b_impairments_from_metrics(
        FirehoseDeliveryMetrics(stream=CoverageStream.FACTS)
    )
    incoming_only = domain_3b_impairments_from_metrics(
        FirehoseDeliveryMetrics(
            stream=CoverageStream.FACTS,
            incoming=((metric_time, 0.0),),
        ),
        as_of=metric_time + timedelta(seconds=1800),
    )
    assert none == ()
    assert incoming_only == ()


def test_single_buffering_period_without_success_is_not_domain_3b():
    t0 = datetime(2026, 7, 18, 12, 15, tzinfo=UTC)
    buffering = domain_3b_impairments_from_metrics(
        FirehoseDeliveryMetrics(
            stream=CoverageStream.FACTS,
            incoming=((t0, 4.0),),
        ),
        as_of=t0 + timedelta(seconds=300),
    )
    before_threshold = domain_3b_impairments_from_metrics(
        FirehoseDeliveryMetrics(
            stream=CoverageStream.FACTS,
            incoming=((t0, 4.0),),
        ),
        as_of=t0 + timedelta(seconds=1799),
    )
    assert buffering == ()
    assert before_threshold == ()


def test_success_within_impairment_window_is_not_missing_freshness_domain_3b():
    t0 = datetime(2026, 7, 18, 12, 15, tzinfo=UTC)
    impairments = domain_3b_impairments_from_metrics(
        FirehoseDeliveryMetrics(
            stream=CoverageStream.FACTS,
            incoming=((t0, 4.0),),
            success=((t0 + timedelta(seconds=900), 1.0),),
        ),
        as_of=t0 + timedelta(seconds=1800),
    )
    assert impairments == ()


def test_freshness_at_or_below_threshold_clears_missing_freshness_fallback():
    t0 = datetime(2026, 7, 18, 12, 15, tzinfo=UTC)
    impairments = domain_3b_impairments_from_metrics(
        FirehoseDeliveryMetrics(
            stream=CoverageStream.FACTS,
            incoming=((t0, 4.0),),
            freshness=((t0 + timedelta(seconds=600), 1800.0),),
        ),
        as_of=t0 + timedelta(seconds=1800),
    )
    assert impairments == ()


def test_unresolved_incoming_through_threshold_is_potential_domain_3b():
    t0 = datetime(2026, 7, 18, 12, 15, tzinfo=UTC)
    impairments = domain_3b_impairments_from_metrics(
        FirehoseDeliveryMetrics(
            stream=CoverageStream.FACTS,
            incoming=((t0, 4.0),),
        ),
        as_of=t0 + timedelta(seconds=1800),
    )
    assert len(impairments) == 1
    gap_time = impairments[0]
    assert gap_time.detection == "MISSING_DELIVERY_TELEMETRY"
    assert gap_time.metric_time == t0 + timedelta(seconds=1800)
    assert gap_time.freshness_seconds == 1800
    oldest = t0 - timedelta(seconds=60 + 86400)
    assert gap_time.interval_start <= oldest
    assert gap_time.interval_end >= t0 + timedelta(seconds=1800)
    record = domain_3b_gap_record(impairment=gap_time, collection_epoch_id=EPOCH)
    assert record.gap_domain is GapDomain.DOMAIN_3B
    assert record.uncertainty_class is UncertaintyClass.POTENTIAL_GAP
    assert record.reason == S3_DESTINATION_FAILURE
    assert record.affected_datasets == ("encounter", "risk", "hazard_version")


def test_open_domain_3b_gap_blocks_evaluate_collection_window():
    as_of = _z(_utc(END) + timedelta(seconds=173760))
    gap = domain_3b_gap_record(
        impairment=domain_3b_impairments_from_metrics(
            FirehoseDeliveryMetrics(
                stream=CoverageStream.FACTS,
                freshness=((datetime(2026, 7, 18, 12, 15, tzinfo=UTC), 1801.0),),
            )
        )[0],
        collection_epoch_id=EPOCH,
    )
    result = _evaluate(as_of=as_of, gap_records=[gap])
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN
    assert result.reason == "unresolved_gap_overlaps_window"
