"""Canonical encoding: sorted keys, integers only, no sets.

Each property here exists because its absence produces a determinism bug that
surfaces a long way from its cause.
"""

from __future__ import annotations

import pytest

from contracts.canonical import NotCanonical, decode, encode, is_canonical, validate


def test_insertion_order_does_not_affect_the_encoding() -> None:
    """Two structurally equal states must produce one hash.

    Without this, a refactor that merely reorders assignments reads as a
    determinism regression.
    """
    assert encode({"a": 1, "b": 2}) == encode({"b": 2, "a": 1})


def test_nested_keys_are_sorted_too() -> None:
    assert encode({"outer": {"z": 1, "a": 2}}) == encode({"outer": {"a": 2, "z": 1}})


def test_encoding_is_compact_and_stable() -> None:
    assert encode({"b": 2, "a": 1}) == b'{"a":1,"b":2}'


@pytest.mark.parametrize("value", [0, -1, 2**63, 2**70, True, False, None, "text", ""])
def test_scalars_that_are_allowed(value) -> None:
    assert decode(encode(value)) == value


def test_large_integers_survive_exactly() -> None:
    """Python is exact here; the browser is not, which is a client-side concern.

    Recorded so the constraint is visible at the encoder: anything that can exceed
    2^53 must reach the client as a string or BigInt.
    """
    value = 2**53 + 1
    assert decode(encode({"seq": value}))["seq"] == value


def test_a_float_is_refused_with_its_path() -> None:
    with pytest.raises(NotCanonical) as excinfo:
        encode({"metrics": {"morale": 0.5}})

    message = str(excinfo.value)
    assert "morale" in message
    assert "R6" in message, "the error should name the requirement it enforces"


def test_a_set_is_refused() -> None:
    with pytest.raises(NotCanonical) as excinfo:
        encode({"informed": {"a", "b"}})
    assert "hash randomisation" in str(excinfo.value)


def test_bytes_are_refused() -> None:
    with pytest.raises(NotCanonical):
        encode({"blob": b"raw"})


def test_a_non_string_key_is_refused() -> None:
    """JSON coerces keys to strings, so 1 and "1" would collide silently."""
    with pytest.raises(NotCanonical) as excinfo:
        encode({1: "one"})
    assert "collide" in str(excinfo.value)


def test_an_arbitrary_object_is_refused() -> None:
    class Thing:
        pass

    with pytest.raises(NotCanonical):
        encode({"thing": Thing()})


def test_a_tuple_encodes_as_an_array() -> None:
    assert encode({"path": (1, 2)}) == encode({"path": [1, 2]})


def test_decode_rejects_a_float_literal_that_reached_the_store() -> None:
    with pytest.raises(NotCanonical):
        decode(b'{"morale":0.5}')


def test_decode_rejects_exponent_notation() -> None:
    with pytest.raises(NotCanonical):
        decode(b'{"cash":1e3}')


def test_decode_rejects_nan_and_infinity_literals() -> None:
    for literal in (b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}'):
        with pytest.raises(NotCanonical):
            decode(literal)


def test_decode_rejects_malformed_bytes() -> None:
    with pytest.raises(NotCanonical):
        decode(b"{not json")


def test_is_canonical_rejects_bytes_that_merely_parse() -> None:
    """Unsorted keys decode fine but would compare unequal under R11.

    Two encodings of the same state must be one byte string, or the whole-log
    comparison reports a difference where there is none.
    """
    assert is_canonical(b'{"a":1,"b":2}')
    assert not is_canonical(b'{"b":2,"a":1}')
    assert not is_canonical(b'{"a": 1, "b": 2}')


def test_validate_accepts_a_deeply_nested_integer_structure() -> None:
    validate({"a": [{"b": [{"c": [1, 2, 3]}]}]})
