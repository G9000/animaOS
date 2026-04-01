"""Strict mode for OpenAI tool schemas.

When strict mode is enabled, OpenAI guarantees the model's tool call
arguments match the JSON schema exactly. This prevents malformed
arguments (e.g. empty {}) that cause validation failures.

Ported from Letta's enable_strict_mode() pattern.
"""

from __future__ import annotations

import copy
from typing import Any


def enable_strict_mode(
    tool_schema: dict[str, Any],
    strict: bool = True,
) -> dict[str, Any]:
    """Enable strict mode on a tool schema.

    When strict=True:
    - Sets ``strict: true`` on the schema
    - Sets ``additionalProperties: false`` on parameters
    - All properties added to ``required`` (OpenAI requirement)
    - Optional properties made nullable to preserve optionality
    - Nested objects/arrays processed recursively

    Returns a deep copy — the original schema is not mutated.
    """
    schema = copy.deepcopy(tool_schema)

    if not strict:
        return schema

    schema["strict"] = True

    parameters = schema.get("parameters", {})
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        return schema

    parameters["additionalProperties"] = False

    properties = parameters.get("properties", {})
    current_required = set(parameters.get("required", []))

    for field_name, field_props in properties.items():
        properties[field_name] = _process_property(field_props)
        if field_name not in current_required:
            _make_nullable(properties[field_name])

    parameters["required"] = list(properties.keys())
    schema["parameters"] = parameters

    return schema


def _process_property(prop: dict[str, Any]) -> dict[str, Any]:
    """Recursively process a property for strict mode."""
    if "anyOf" in prop:
        prop["anyOf"] = [_process_property(opt) for opt in prop["anyOf"]]
        return prop

    if "type" not in prop:
        return prop

    param_type = prop["type"]

    if isinstance(param_type, list):
        return prop

    if param_type == "object":
        if "properties" in prop:
            for key, value in prop["properties"].items():
                prop["properties"][key] = _process_property(value)
            prop["additionalProperties"] = False
            prop["required"] = list(prop["properties"].keys())
        return prop

    if param_type == "array":
        if "items" in prop:
            prop["items"] = _process_property(prop["items"])
        return prop

    return prop


def _make_nullable(field_props: dict[str, Any]) -> None:
    """Make a field nullable by adding 'null' to its type. Modifies in place."""
    if "type" in field_props:
        field_type = field_props["type"]
        if isinstance(field_type, list):
            if "null" not in field_type:
                field_type.append("null")
        elif field_type != "null":
            field_props["type"] = [field_type, "null"]
    elif "anyOf" in field_props:
        has_null = any(opt.get("type") == "null" for opt in field_props["anyOf"])
        if not has_null:
            field_props["anyOf"].append({"type": "null"})
    elif "$ref" in field_props:
        ref_value = field_props.pop("$ref")
        field_props["anyOf"] = [{"$ref": ref_value}, {"type": "null"}]
    else:
        field_props["type"] = "null"
