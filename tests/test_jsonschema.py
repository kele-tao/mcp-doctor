"""JSON Schema validation tests, including the schema-document checks."""

from __future__ import annotations

import pytest

from mcp_doctor.jsonschema import (
    SchemaError,
    schema_summary,
    type_name,
    validate_arguments,
    validate_schema,
)


def test_type_mismatch_reports_path_and_actual_type():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    errors = validate_arguments(schema, {"a": "x"})
    assert len(errors) == 1
    assert errors[0].path == "$.a"
    assert "expected integer, got string" in errors[0].message


def test_required_and_additional_properties():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "integer"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    errors = validate_arguments(schema, {})
    assert "missing required property 'a'" in errors[0].message
    errors = validate_arguments(schema, {"a": 1, "b": 2})
    assert "unexpected properties: 'b'" in errors[0].message


def test_numeric_and_string_bounds():
    schema = {
        "type": "object",
        "properties": {
            "n": {"type": "integer", "minimum": 1, "maximum": 5, "multipleOf": 2},
            "s": {"type": "string", "minLength": 2, "maxLength": 4, "pattern": "^[a-z]+$"},
        },
    }
    assert validate_arguments(schema, {"n": 0})[0].message == "must be >= 1"
    assert validate_arguments(schema, {"n": 3})[0].message == "must be a multiple of 2"
    assert "minLength" in validate_arguments(schema, {"s": "a"})[0].message
    assert "does not match pattern" in validate_arguments(schema, {"s": "AB"})[0].message


def test_array_constraints():
    schema = {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 2,
        "maxItems": 3,
        "uniqueItems": True,
    }
    assert "needs at least" in validate_arguments(schema, ["a"])[0].message
    assert "allows at most" in validate_arguments(schema, ["a", "b", "c", "d"])[0].message
    assert "must be unique" in validate_arguments(schema, ["a", "a"])[0].message
    errors = validate_arguments(schema, ["a", 2])
    assert errors[0].path == "$[1]"


def test_enum_const_and_union_types():
    schema = {"type": ["string", "null"], "enum": ["a", "b", None]}
    assert validate_arguments(schema, "a") == []
    assert validate_arguments(schema, None) == []
    assert "must be one of" in validate_arguments(schema, "c")[0].message
    assert validate_arguments({"const": 3}, 3) == []
    assert "must equal 3" in validate_arguments({"const": 3}, 4)[0].message


def test_combinators():
    assert validate_arguments({"anyOf": [{"type": "string"}, {"type": "integer"}]}, 3) == []
    assert (
        "does not match anyOf"
        in validate_arguments({"anyOf": [{"type": "string"}, {"type": "integer"}]}, [])[0].message
    )
    errors = validate_arguments({"oneOf": [{"type": "integer"}, {"type": "number"}]}, 3)
    assert "exactly one oneOf branch" in errors[0].message
    errors = validate_arguments({"allOf": [{"minimum": 5}, {"maximum": 3}]}, 4)
    assert len(errors) == 2
    assert "must not match" in validate_arguments({"not": {"type": "string"}}, "x")[0].message


def test_local_ref_resolution():
    schema = {
        "$defs": {
            "point": {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}
        },
        "type": "object",
        "properties": {"p": {"$ref": "#/$defs/point"}},
    }
    assert validate_arguments(schema, {"p": {"x": 1}}) == []
    assert validate_arguments(schema, {"p": {}})[0].path == "$.p"
    assert (
        "unresolvable reference"
        in validate_schema({"properties": {"p": {"$ref": "#/$defs/missing"}}})[0].message
    )


def test_schema_document_checks():
    assert validate_schema({"type": "object"}) == []
    problems = validate_schema({"type": "intger"})
    assert "unknown type" in problems[0].message
    assert any("unknown schema keyword" in p.message for p in validate_schema({"typo": 1}))
    assert any("invalid regular expression" in p.message for p in validate_schema({"pattern": "["}))
    assert any("duplicate names" in p.message for p in validate_schema({"required": ["a", "a"]}))
    assert any("oneOf" in p.message for p in validate_schema({"oneOf": []}))
    assert validate_schema("not-a-schema")[0].message.startswith("schema must be")


def test_booleans_are_not_integers():
    assert (
        validate_arguments({"type": "integer"}, True)[0].message == "expected integer, got boolean"
    )
    assert (
        validate_arguments({"type": "number"}, False)[0].message == "expected number, got boolean"
    )


def test_type_name_and_summary():
    assert type_name(None) == "null"
    assert type_name(1.5) == "number"
    assert type_name(2.0) == "integer-float"
    summary = schema_summary(
        {
            "type": "object",
            "required": ["a"],
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "array", "items": {"type": "integer"}},
                "c": {"oneOf": [{"type": "string"}, {"type": "null"}]},
            },
        }
    )
    assert summary["properties"] == 3
    assert summary["required"] == 1
    assert summary["branches"] == 2
    assert summary["depth"] >= 3


def test_false_schema_rejects_everything():
    errors = validate_arguments(False, 1)
    assert errors[0].message == "no value is allowed here"
    assert isinstance(errors[0], SchemaError)


@pytest.mark.parametrize("value", ["", 0, [], {}, False])
def test_true_schema_accepts_everything(value):
    assert validate_arguments(True, value) == []
