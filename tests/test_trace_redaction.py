"""Structural redaction for Phoenix tool-response tracing."""

from app.observability import redact_values


def test_scalars_become_type_tokens():
    assert redact_values("hello") == "<str:5>"
    assert redact_values(42) == "<int>"
    assert redact_values(3.14) == "<float>"
    # bool is a subclass of int — must not be reported as <int>.
    assert redact_values(True) == "<bool>"
    assert redact_values(None) is None


def test_dict_keys_preserved_values_masked():
    out = redact_values({"name": "Acme", "cost": 4210.5, "active": True})
    assert out == {"name": "<str:4>", "cost": "<float>", "active": "<bool>"}


def test_list_collapses_to_shape_and_count():
    assert redact_values([{"a": 1}, {"a": 2}, {"a": 3}]) == [
        {"a": "<int>"},
        "<+2 more items>",
    ]
    assert redact_values([]) == []
    assert redact_values([{"a": 1}]) == [{"a": "<int>"}]


def test_nested_structure_is_preserved():
    payload = {
        "ok": True,
        "fact_pack": {
            "account": {"name": "Jump", "id": "123"},
            "campaigns": [{"name": "Brand", "cost": 100.0}],
        },
    }
    assert redact_values(payload) == {
        "ok": "<bool>",
        "fact_pack": {
            "account": {"name": "<str:4>", "id": "<str:3>"},
            "campaigns": [{"name": "<str:5>", "cost": "<float>"}],
        },
    }
