"""A zero-dependency JSON Schema validator, scoped to what MCP tools actually use.

This is deliberately not a full draft 2020-12 implementation. It covers the
keyword set that MCP servers put in inputSchema/outputSchema: type, properties,
required, additionalProperties, items, enum, const, numeric and string bounds,
pattern, oneOf/anyOf/allOf/not, and local $ref pointers. It also validates the
schema document itself, which is how mcp-doctor catches tools that ship an
inputSchema a JSON-Schema-2020-12 implementation would reject.

Every failure is a SchemaError carrying the JSON path, so the CLI can print
"arguments.a: expected integer, got string" instead of a wall of text.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

JSON_TYPES = {"null", "boolean", "object", "array", "number", "integer", "string"}

SUPPORTED_KEYWORDS = {
    "$comment",
    "$defs",
    "$id",
    "$ref",
    "$schema",
    "additionalProperties",
    "allOf",
    "anyOf",
    "const",
    "default",
    "definitions",
    "deprecated",
    "description",
    "enum",
    "examples",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "items",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "multipleOf",
    "not",
    "oneOf",
    "pattern",
    "properties",
    "readOnly",
    "required",
    "title",
    "type",
    "uniqueItems",
    "writeOnly",
}


class SchemaError(Exception):
    """A validation failure at a specific JSON path."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path or "$"
        self.message = message
        super().__init__(f"{self.path}: {message}")


def type_name(value: Any) -> str:
    """Return the JSON Schema type name of a runtime value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number" if not value.is_integer() else "integer-float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (
            isinstance(value, float) and value.is_integer()
        )
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return False


def resolve_ref(root: dict, ref: str) -> dict:
    """Resolve a local JSON pointer reference against the root schema."""
    if not ref.startswith("#"):
        raise SchemaError("$ref", f"only local references are supported, got {ref!r}")
    pointer = ref[1:]
    if pointer in ("", "/"):
        return root
    if not pointer.startswith("/"):
        raise SchemaError("$ref", f"malformed JSON pointer {ref!r}")
    node: Any = root
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            raise SchemaError("$ref", f"unresolvable reference {ref!r}")
    if not isinstance(node, dict):
        raise SchemaError("$ref", f"reference {ref!r} does not point at a schema object")
    return node


def validate_schema(schema: Any, path: str = "$") -> list[SchemaError]:
    """Static sanity checks on a schema document itself.

    Returns a list of problems rather than raising, because the conformance
    suite wants to report every schema defect of a server in one pass.
    """
    errors: list[SchemaError] = []
    root = schema if isinstance(schema, dict) else {}
    _check_schema(schema, path, root, errors)
    return errors


def _check_schema(schema: Any, path: str, root: dict, errors: list[SchemaError]) -> None:
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        errors.append(SchemaError(path, "schema must be an object or a boolean"))
        return
    for keyword in schema:
        if keyword not in SUPPORTED_KEYWORDS:
            errors.append(SchemaError(path, f"unknown schema keyword {keyword!r}"))
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str):
            errors.append(SchemaError(path, "$ref must be a string"))
        else:
            try:
                resolve_ref(root, ref)
            except SchemaError as exc:
                errors.append(SchemaError(path, exc.message))
        return
    declared = schema.get("type")
    if declared is not None:
        types = declared if isinstance(declared, list) else [declared]
        if not types:
            errors.append(SchemaError(path, "type array must not be empty"))
        for item in types:
            if not isinstance(item, str) or item not in JSON_TYPES:
                errors.append(SchemaError(path, f"unknown type {item!r}"))
    for keyword in ("minLength", "maxLength", "minItems", "maxItems"):
        if keyword in schema:
            raw = schema[keyword]
            if isinstance(raw, bool) or not isinstance(raw, int):
                errors.append(SchemaError(path, f"{keyword} must be an integer"))
            elif raw < 0:
                errors.append(SchemaError(path, f"{keyword} must be >= 0"))
    if "pattern" in schema:
        if not isinstance(schema["pattern"], str):
            errors.append(SchemaError(path, "pattern must be a string"))
        else:
            try:
                re.compile(schema["pattern"])
            except re.error as exc:
                errors.append(SchemaError(path, f"invalid regular expression: {exc}"))
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
            errors.append(SchemaError(path, "required must be an array of strings"))
        elif len(set(required)) != len(required):
            errors.append(SchemaError(path, "required contains duplicate names"))
    if "properties" in schema and not isinstance(schema["properties"], dict):
        errors.append(SchemaError(path, "properties must be an object"))
    if "items" in schema and not isinstance(schema["items"], (dict, list)):
        errors.append(SchemaError(path, "items must be a schema or an array of schemas"))
    if "enum" in schema:
        if not isinstance(schema["enum"], list):
            errors.append(SchemaError(path, "enum must be an array"))
        elif not schema["enum"]:
            errors.append(SchemaError(path, "enum must not be empty"))
    for keyword in ("oneOf", "anyOf", "allOf"):
        if keyword in schema:
            branches = schema[keyword]
            if not isinstance(branches, list) or not branches:
                errors.append(SchemaError(path, f"{keyword} must be a non-empty array"))
    for keyword in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        if keyword in schema:
            raw = schema[keyword]
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                errors.append(SchemaError(path, f"{keyword} must be a number"))
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, subschema in properties.items():
            _check_schema(subschema, f"{path}.{name}", root, errors)
    items = schema.get("items")
    if isinstance(items, dict):
        _check_schema(items, f"{path}[]", root, errors)
    elif isinstance(items, list):
        for index, subschema in enumerate(items):
            _check_schema(subschema, f"{path}[{index}]", root, errors)
    for keyword in ("oneOf", "anyOf", "allOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for index, subschema in enumerate(branches):
                _check_schema(subschema, f"{path}.{keyword}[{index}]", root, errors)
    if "not" in schema:
        _check_schema(schema["not"], f"{path}.not", root, errors)
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        _check_schema(additional, f"{path}.*", root, errors)


def validate_arguments(schema: dict, value: Any, root: dict | None = None) -> list[SchemaError]:
    """Validate a value against a schema and return every error found."""
    document = root if root is not None else schema
    errors: list[SchemaError] = []
    _validate(schema, value, "$", document, errors)
    return errors


def _validate(schema: Any, value: Any, path: str, root: dict, errors: list[SchemaError]) -> None:
    if schema is True or schema == {}:
        return
    if schema is False:
        errors.append(SchemaError(path, "no value is allowed here"))
        return
    if not isinstance(schema, dict):
        errors.append(SchemaError(path, "invalid schema fragment"))
        return
    if "$ref" in schema:
        try:
            target = resolve_ref(root, schema["$ref"])
        except SchemaError as exc:
            errors.append(SchemaError(path, exc.message))
            return
        _validate(target, value, path, root, errors)
        return

    expected = schema.get("type")
    if expected is not None:
        if isinstance(expected, str):
            ok = _matches_type(value, expected)
            label = expected
        else:
            ok = any(_matches_type(value, item) for item in expected)
            label = " or ".join(str(item) for item in expected)
        if not ok:
            errors.append(SchemaError(path, f"expected {label}, got {type_name(value)}"))
            return

    if "const" in schema and value != schema["const"]:
        errors.append(SchemaError(path, f"must equal {schema['const']!r}"))
    if "enum" in schema and isinstance(schema["enum"], list) and value not in schema["enum"]:
        allowed = ", ".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"][:8])
        errors.append(SchemaError(path, f"must be one of [{allowed}]"))

    for keyword, comparator, label in (
        ("minimum", lambda a, b: a >= b, ">="),
        ("maximum", lambda a, b: a <= b, "<="),
        ("exclusiveMinimum", lambda a, b: a > b, ">"),
        ("exclusiveMaximum", lambda a, b: a < b, "<"),
    ):
        if keyword in schema and isinstance(value, (int, float)) and not isinstance(value, bool):
            limit = schema[keyword]
            if isinstance(limit, (int, float)) and not comparator(value, limit):
                errors.append(SchemaError(path, f"must be {label} {limit}"))
    if "multipleOf" in schema and isinstance(value, (int, float)) and not isinstance(value, bool):
        divisor = schema["multipleOf"]
        if isinstance(divisor, (int, float)) and divisor:
            quotient = value / divisor
            if not math.isclose(quotient, round(quotient), abs_tol=1e-9):
                errors.append(SchemaError(path, f"must be a multiple of {divisor}"))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(SchemaError(path, f"shorter than minLength {schema['minLength']}"))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(SchemaError(path, f"longer than maxLength {schema['maxLength']}"))
        if "pattern" in schema and isinstance(schema["pattern"], str):
            try:
                if re.search(schema["pattern"], value) is None:
                    errors.append(
                        SchemaError(path, f"does not match pattern {schema['pattern']!r}")
                    )
            except re.error:
                pass

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(SchemaError(path, f"needs at least {schema['minItems']} items"))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(SchemaError(path, f"allows at most {schema['maxItems']} items"))
        if schema.get("uniqueItems"):
            seen: list[Any] = []
            for item in value:
                if item in seen:
                    errors.append(SchemaError(path, f"items must be unique, {item!r} repeats"))
                    break
                seen.append(item)
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate(items, item, f"{path}[{index}]", root, errors)
        elif isinstance(items, list):
            for index, item in enumerate(value):
                if index < len(items):
                    _validate(items[index], item, f"{path}[{index}]", root, errors)

    if isinstance(value, dict):
        properties = schema.get("properties")
        required = schema.get("required")
        if isinstance(required, list):
            for name in required:
                if name not in value:
                    errors.append(SchemaError(path, f"missing required property {name!r}"))
        if isinstance(properties, dict):
            for name, subschema in properties.items():
                if name in value:
                    _validate(subschema, value[name], f"{path}.{name}", root, errors)
        additional = schema.get("additionalProperties", True)
        if additional is False and isinstance(properties, dict):
            extras = sorted(set(value) - set(properties))
            if extras:
                errors.append(
                    SchemaError(
                        path, "unexpected properties: " + ", ".join(repr(x) for x in extras)
                    )
                )
        elif isinstance(additional, dict):
            known = set(properties) if isinstance(properties, dict) else set()
            for name, subvalue in value.items():
                if name not in known:
                    _validate(additional, subvalue, f"{path}.{name}", root, errors)

    for keyword, mode in (("allOf", "all"), ("anyOf", "any"), ("oneOf", "one")):
        branches = schema.get(keyword)
        if not isinstance(branches, list) or not branches:
            continue
        results: list[list[SchemaError]] = []
        for subschema in branches:
            local: list[SchemaError] = []
            _validate(subschema, value, path, root, local)
            results.append(local)
        passed = sum(1 for found in results if not found)
        if mode == "all":
            for found in results:
                errors.extend(found)
        elif mode == "any" and passed == 0:
            detail = results[0][0].message if results and results[0] else "no branch matched"
            errors.append(SchemaError(path, f"does not match anyOf ({detail})"))
        elif mode == "one" and passed != 1:
            errors.append(
                SchemaError(path, f"must match exactly one oneOf branch, matched {passed}")
            )
    if "not" in schema and isinstance(schema["not"], dict):
        local_not: list[SchemaError] = []
        _validate(schema["not"], value, path, root, local_not)
        if not local_not:
            errors.append(SchemaError(path, "matches a schema it must not match"))


def schema_summary(schema: dict) -> dict:
    """Cheap structural statistics used by inspect tables and the HTML report."""
    counts = {"properties": 0, "required": 0, "refs": 0, "enums": 0, "branches": 0, "depth": 1}

    def walk(node: Any, level: int) -> None:
        if not isinstance(node, dict):
            return
        counts["depth"] = max(counts["depth"], level)
        properties = node.get("properties")
        if isinstance(properties, dict):
            counts["properties"] += len(properties)
            for subschema in properties.values():
                walk(subschema, level + 1)
        if isinstance(node.get("required"), list):
            counts["required"] += len(node["required"])
        if "$ref" in node:
            counts["refs"] += 1
        if isinstance(node.get("enum"), list):
            counts["enums"] += 1
        for keyword in ("oneOf", "anyOf", "allOf"):
            branches = node.get(keyword)
            if isinstance(branches, list):
                counts["branches"] += len(branches)
                for subschema in branches:
                    walk(subschema, level + 1)
        if isinstance(node.get("items"), dict):
            walk(node["items"], level + 1)

    walk(schema, 1)
    return counts
