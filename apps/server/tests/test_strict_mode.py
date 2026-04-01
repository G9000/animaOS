from __future__ import annotations

from anima_server.services.agent.strict_mode import enable_strict_mode


def test_strict_mode_sets_flags() -> None:
    schema = {
        "name": "my_tool",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
            },
            "required": ["message"],
        },
    }
    result = enable_strict_mode(schema)
    assert result["strict"] is True
    assert result["parameters"]["additionalProperties"] is False
    assert result["parameters"]["required"] == ["message"]


def test_strict_mode_makes_optional_fields_nullable() -> None:
    schema = {
        "name": "my_tool",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "tags": {"type": "string"},
            },
            "required": ["message"],
        },
    }
    result = enable_strict_mode(schema)
    assert set(result["parameters"]["required"]) == {"message", "tags"}
    tags_type = result["parameters"]["properties"]["tags"]["type"]
    assert tags_type == ["string", "null"]
    assert result["parameters"]["properties"]["message"]["type"] == "string"


def test_strict_mode_processes_nested_objects() -> None:
    schema = {
        "name": "my_tool",
        "parameters": {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                    },
                },
            },
            "required": ["config"],
        },
    }
    result = enable_strict_mode(schema)
    nested = result["parameters"]["properties"]["config"]
    assert nested["additionalProperties"] is False
    assert nested["required"] == ["key"]


def test_strict_mode_does_not_mutate_original() -> None:
    schema = {
        "name": "my_tool",
        "parameters": {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
    }
    result = enable_strict_mode(schema)
    assert "strict" not in schema
    assert "additionalProperties" not in schema.get("parameters", {})
    assert result is not schema


def test_strict_mode_disabled() -> None:
    schema = {
        "name": "my_tool",
        "parameters": {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
    }
    result = enable_strict_mode(schema, strict=False)
    assert "strict" not in result
