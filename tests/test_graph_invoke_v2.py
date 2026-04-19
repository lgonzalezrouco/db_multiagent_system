"""Unit tests for ``graph.invoke_v2.unwrap_graph_v2``."""

from __future__ import annotations

import pytest

from graph.invoke_v2 import unwrap_graph_v2


def test_unwrap_graph_v2_plain_dict() -> None:
    state = {"last_result": None, "last_error": None}
    out, intr = unwrap_graph_v2(state)
    assert out is state
    assert intr == ()


def test_unwrap_graph_v2_v2_envelope() -> None:
    class _Intr:
        value = {"kind": "schema_review", "draft": {}}

    class _Envelope:
        value = {"steps": ["x"]}
        interrupts = (_Intr(),)

    state, intr = unwrap_graph_v2(_Envelope())
    assert state == {"steps": ["x"]}
    assert len(intr) == 1


def test_unwrap_graph_v2_bad_envelope_raises() -> None:
    class _Bad:
        value = "not-a-dict"
        interrupts = ()

    with pytest.raises(TypeError, match="unexpected graph result type"):
        unwrap_graph_v2(_Bad())
