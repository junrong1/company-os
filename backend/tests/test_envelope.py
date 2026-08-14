"""The event envelope: the contract every later unit codes against.

The tests that matter most here are the two boundary ones — the hashed/metadata
classification, and the closed kind set. Both protect against failures that are
invisible at the point they are introduced and only surface as an unexplainable
divergence much later.
"""

from __future__ import annotations

import dataclasses

import pytest

from contracts import canonical
from contracts.envelope import (
    EVENT_SCHEMA_VERSION,
    HASHED_FIELDS,
    KIND_SCHEMA_VERSIONS,
    METADATA_FIELDS,
    Envelope,
    EnvelopeInvalid,
    EventKind,
    UnknownEventKind,
    build,
    canonical_log_projection,
    request_id_for,
)

RUN = "11111111-1111-5111-8111-111111111111"
RULES = "rules-abc123"


def an_envelope(**overrides) -> Envelope:
    fields = {
        "seq": 7,
        "tick": 42,
        "kind": EventKind.WORK_ASSIGNED,
        "rules_ver": RULES,
        "payload": {"item": "ops-1", "assignee": "nina", "effort_hours": 12},
        "run_id": RUN,
    }
    fields.update(overrides)
    return build(**fields)


# --- classification: the field set is the contract -------------------------


def test_every_envelope_field_is_classified_hashed_or_metadata() -> None:
    """R11: a new field cannot be added until it is classified.

    This is the mechanism, not a reminder. Add a field to Envelope and this fails
    until you decide which group it belongs to — and the decision matters in both
    directions. Metadata in the hashed set makes two runs of one seed compare
    unequal when they are identical; a hashed field in the metadata set makes two
    different runs compare equal, which passes the replay test while replay is
    broken.
    """
    declared = {field.name for field in dataclasses.fields(Envelope)}
    classified = HASHED_FIELDS | METADATA_FIELDS

    assert declared == classified, (
        f"unclassified: {sorted(declared - classified)}; "
        f"classified but absent: {sorted(classified - declared)}"
    )


def test_the_two_groups_do_not_overlap() -> None:
    assert not (HASHED_FIELDS & METADATA_FIELDS)


def test_hashed_projection_covers_exactly_the_hashed_fields() -> None:
    envelope = an_envelope()
    assert len(envelope.hashed_projection()) == len(HASHED_FIELDS)


# --- round trip -----------------------------------------------------------


def test_envelope_round_trips_with_sequence_tick_and_both_versions() -> None:
    original = an_envelope(seq=1234, tick=98765)

    restored = Envelope.from_dict(original.to_dict())

    assert restored.seq == 1234
    assert restored.tick == 98765
    assert restored.schema_ver == KIND_SCHEMA_VERSIONS[EventKind.WORK_ASSIGNED]
    assert restored.rules_ver == RULES
    assert restored == original
    assert restored.decoded_payload() == original.decoded_payload()


def test_payload_survives_a_round_trip_byte_for_byte() -> None:
    original = an_envelope(payload={"z": 1, "a": 2, "m": {"nested": [1, 2, 3]}})
    restored = Envelope.from_dict(original.to_dict())
    assert restored.payload == original.payload


def test_sequence_and_tick_above_2_53_survive() -> None:
    """uint64 fields, and the client reads them.

    Python carries these exactly; `JSON.parse` in the browser would not. The value
    is preserved here so the failure, when it comes, is located on the client side
    where the plan says the guard belongs.
    """
    big = 2**53 + 7
    restored = Envelope.from_dict(an_envelope(seq=big, tick=big + 1).to_dict())
    assert restored.seq == big
    assert restored.tick == big + 1


# --- the closed kind set --------------------------------------------------


def test_an_unknown_event_kind_is_rejected_not_skipped() -> None:
    """A skipped event is a divergence with no cause attached to it.

    It would surface at the next day-boundary hash as a mismatch, with nothing
    pointing at the event that was dropped.
    """
    raw = an_envelope().to_dict()
    raw["kind"] = 9999

    with pytest.raises(UnknownEventKind) as excinfo:
        Envelope.from_dict(raw)

    assert "9999" in str(excinfo.value)


def test_the_unspecified_kind_is_rejected() -> None:
    """Catches a default-constructed envelope, which proto3 makes easy to produce."""
    with pytest.raises(UnknownEventKind):
        Envelope(
            seq=1,
            tick=1,
            kind=EventKind.UNSPECIFIED,
            schema_ver=1,
            rules_ver=RULES,
            payload=b"{}",
            run_id=RUN,
        )


def test_every_kind_except_unspecified_has_a_payload_schema_version() -> None:
    missing = [
        kind.name
        for kind in EventKind
        if kind is not EventKind.UNSPECIFIED and kind not in KIND_SCHEMA_VERSIONS
    ]
    assert not missing, f"no payload schema version for: {missing}"


def test_building_an_unregistered_kind_refuses_rather_than_guessing() -> None:
    with pytest.raises(UnknownEventKind):
        build(
            seq=1,
            tick=1,
            kind=EventKind.UNSPECIFIED,
            rules_ver=RULES,
            payload={},
            run_id=RUN,
        )


def test_the_kind_table_is_pinned() -> None:
    """Adding a kind should be a deliberate edit, not a side effect.

    The count is asserted so that a new kind arriving with a new mechanic is a
    conscious change to this table rather than something that appears in the log
    unannounced.
    """
    # 26 since Phase 2 added QUESTION_ANSWERED for the ask mechanic.
    assert len(KIND_SCHEMA_VERSIONS) == 26
    assert len(EventKind) == 27  # the 26 above, plus UNSPECIFIED


# --- integers only (R6) ---------------------------------------------------


def test_a_payload_containing_a_float_is_rejected() -> None:
    with pytest.raises(canonical.NotCanonical) as excinfo:
        an_envelope(payload={"morale": 0.5})

    message = str(excinfo.value)
    assert "float" in message
    assert "morale" in message, "the error should name the offending path"


@pytest.mark.parametrize(
    "payload",
    [
        {"nested": {"deep": [1, 2, 3.5]}},
        {"cash": float("nan")},
        {"cash": float("inf")},
        [1, 2, 0.1],
    ],
)
def test_floats_are_rejected_wherever_they_hide(payload) -> None:
    with pytest.raises(canonical.NotCanonical):
        an_envelope(payload=payload)


def test_a_set_is_rejected_because_its_order_is_not_a_property_of_the_run() -> None:
    with pytest.raises(canonical.NotCanonical) as excinfo:
        an_envelope(payload={"informed": {"marcus", "dana"}})
    assert "set" in str(excinfo.value)


def test_a_float_that_reached_the_store_is_still_rejected_on_read() -> None:
    """Validated on read as well as before append.

    A malformed value that got in becomes a permanent logged fact that every
    future fold reproduces, and the report would present it with a resolvable
    event behind it — which makes it look more credible, not less.
    """
    raw = an_envelope().to_dict()
    raw["payload"] = b'{"morale":0.5}'

    with pytest.raises(canonical.NotCanonical):
        Envelope.from_dict(raw)


# --- causation (the metadata group) ---------------------------------------


def test_an_event_produced_from_a_command_carries_that_commands_id() -> None:
    command_id = "cmd-7f3a"
    envelope = an_envelope(command_id=command_id)

    assert envelope.command_id == command_id
    assert Envelope.from_dict(envelope.to_dict()).command_id == command_id


def test_changing_a_metadata_field_does_not_change_replay_identity() -> None:
    """The property that makes R11's comparison possible at all.

    Ingest time, correlation ids and producing host differ between two runs of the
    same seed. If any of them entered the hashed projection, two identical runs
    would compare unequal.
    """
    # `ingested_at` is set by the store, not by a producer, so `build()` does not
    # accept it. Stamping it afterwards is exactly what the store does.
    baseline = dataclasses.replace(
        an_envelope(command_id="cmd-a", request_id=""), ingested_at="2026-01-01T00:00:00Z"
    )
    altered = dataclasses.replace(
        an_envelope(
            command_id="cmd-b",
            request_id="99999999-9999-5999-8999-999999999999",
        ),
        ingested_at="2026-12-31T23:59:59Z",
    )

    assert baseline.hashed_projection() == altered.hashed_projection()
    assert canonical_log_projection([baseline]) == canonical_log_projection([altered])


def test_changing_a_hashed_field_does_change_replay_identity() -> None:
    """The other half. Without this, the test above could pass on a stub."""
    baseline = an_envelope(tick=1)
    altered = an_envelope(tick=2)

    assert baseline.hashed_projection() != altered.hashed_projection()
    assert canonical_log_projection([baseline]) != canonical_log_projection([altered])


def test_run_id_is_metadata_so_a_fork_compares_equal_to_its_parent_prefix() -> None:
    """run_id sits outside the hashed set deliberately.

    A forked child copies its parent's prefix; those events describe the same
    history and must project identically despite belonging to different runs.
    """
    parent = an_envelope(run_id=RUN)
    child = an_envelope(run_id="22222222-2222-5222-8222-222222222222")

    assert parent.hashed_projection() == child.hashed_projection()


# --- request ids ----------------------------------------------------------


def test_request_id_is_deterministic_for_one_run_and_sequence() -> None:
    assert request_id_for(RUN, 41) == request_id_for(RUN, 41)


def test_request_id_differs_per_sequence() -> None:
    assert request_id_for(RUN, 41) != request_id_for(RUN, 42)


def test_a_fork_derives_a_child_scoped_request_id() -> None:
    """R3: parent and child must never hold the same in-flight id.

    A per-run counter would collide across a fork and let an answer intended for
    the parent be accepted by the child.
    """
    child_run = "22222222-2222-5222-8222-222222222222"
    assert request_id_for(RUN, 41) != request_id_for(child_run, 41)


def test_request_id_is_a_valid_uuid5() -> None:
    import uuid

    parsed = uuid.UUID(request_id_for(RUN, 41))
    assert parsed.version == 5


# --- envelope invariants --------------------------------------------------


def test_rules_version_is_required() -> None:
    """Every integrity guard keys on it.

    An empty value would let a stale snapshot compare as current, and
    fold-from-snapshot would then diverge from fold-from-zero while the guard
    reported a match.
    """
    with pytest.raises(EnvelopeInvalid) as excinfo:
        an_envelope(rules_ver="")
    assert "rules_ver" in str(excinfo.value)


def test_run_id_is_required() -> None:
    with pytest.raises(EnvelopeInvalid):
        an_envelope(run_id="")


@pytest.mark.parametrize("field", ["seq", "tick"])
def test_sequence_and_tick_are_unsigned(field: str) -> None:
    with pytest.raises(EnvelopeInvalid):
        an_envelope(**{field: -1})


def test_envelope_schema_version_is_reported_and_positive() -> None:
    assert isinstance(EVENT_SCHEMA_VERSION, int)
    assert EVENT_SCHEMA_VERSION >= 1


def test_the_envelope_is_immutable() -> None:
    """A logged fact does not change after the fact."""
    envelope = an_envelope()
    with pytest.raises(dataclasses.FrozenInstanceError):
        envelope.seq = 99  # type: ignore[misc]
