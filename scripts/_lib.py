from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ValidationError(Exception):
    pass


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, float)


def _json_path_join(path: str, token: str) -> str:
    if token.startswith("["):
        return f"{path}{token}"
    if path == "$":
        return f"$.{token}"
    return f"{path}.{token}"


def _resolve_json_pointer(root_schema: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise ValidationError(f"Only local refs supported, got: {ref}")
    node: Any = root_schema
    for part in ref.removeprefix("#/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise ValidationError(f"Unresolvable $ref: {ref}")
        node = node[part]
    return node


def _matches_schema(instance: Any, schema: dict[str, Any], root_schema: dict[str, Any]) -> bool:
    return not validate_jsonschema(instance, schema, root_schema=root_schema, path="$", _for_if_check=True)


def validate_jsonschema(
    instance: Any,
    schema: dict[str, Any],
    *,
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
    _for_if_check: bool = False,
) -> list[str]:
    root_schema = schema if root_schema is None else root_schema
    errors: list[str] = []

    if "$ref" in schema:
        try:
            target = _resolve_json_pointer(root_schema, schema["$ref"])
        except ValidationError as e:
            return [f"{path}: {e}"]
        return validate_jsonschema(
            instance, target, root_schema=root_schema, path=path, _for_if_check=_for_if_check
        )

    if "allOf" in schema:
        for idx, sub in enumerate(schema["allOf"]):
            if isinstance(sub, dict):
                errors.extend(
                    validate_jsonschema(
                        instance, sub, root_schema=root_schema, path=path, _for_if_check=_for_if_check
                    )
                )
            else:
                errors.append(f"{path}: allOf[{idx}] is not an object")

    if "if" in schema and "then" in schema:
        if_schema = schema.get("if")
        then_schema = schema.get("then")
        if isinstance(if_schema, dict) and isinstance(then_schema, dict):
            if _matches_schema(instance, if_schema, root_schema):
                errors.extend(
                    validate_jsonschema(
                        instance, then_schema, root_schema=root_schema, path=path, _for_if_check=_for_if_check
                    )
                )
        else:
            errors.append(f"{path}: if/then must be objects")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = expected_type if isinstance(expected_type, list) else [expected_type]
        ok = False
        for t in allowed:
            if t == "object" and isinstance(instance, dict):
                ok = True
            elif t == "array" and isinstance(instance, list):
                ok = True
            elif t == "string" and isinstance(instance, str):
                ok = True
            elif t == "integer" and _is_integer(instance):
                ok = True
            elif t == "number" and _is_number(instance):
                ok = True
            elif t == "boolean" and isinstance(instance, bool):
                ok = True
            elif t == "null" and instance is None:
                ok = True
        if not ok:
            errors.append(f"{path}: expected type {allowed}, got {type(instance).__name__}")
            return errors

    if "enum" in schema:
        enum_vals = schema["enum"]
        if instance not in enum_vals:
            errors.append(f"{path}: value {instance!r} not in enum")

    if "const" in schema:
        if instance != schema["const"]:
            errors.append(f"{path}: value {instance!r} != const {schema['const']!r}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < int(schema["minLength"]):
            errors.append(f"{path}: string length {len(instance)} < minLength {schema['minLength']}")
        if "pattern" in schema:
            pat = schema["pattern"]
            if not re.search(pat, instance):
                errors.append(f"{path}: string does not match pattern {pat!r}")

    if _is_number(instance):
        if "minimum" in schema and float(instance) < float(schema["minimum"]):
            errors.append(f"{path}: number {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and float(instance) > float(schema["maximum"]):
            errors.append(f"{path}: number {instance} > maximum {schema['maximum']}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < int(schema["minItems"]):
            errors.append(f"{path}: array length {len(instance)} < minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > int(schema["maxItems"]):
            errors.append(f"{path}: array length {len(instance)} > maxItems {schema['maxItems']}")
        if "items" in schema and isinstance(schema["items"], dict):
            item_schema = schema["items"]
            for i, item in enumerate(instance):
                errors.extend(
                    validate_jsonschema(
                        item,
                        item_schema,
                        root_schema=root_schema,
                        path=_json_path_join(path, f"[{i}]"),
                        _for_if_check=_for_if_check,
                    )
                )

    if isinstance(instance, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if key not in instance:
                    errors.append(f"{path}: missing required property {key!r}")

        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, prop_schema in properties.items():
                if key in instance and isinstance(prop_schema, dict):
                    errors.extend(
                        validate_jsonschema(
                            instance[key],
                            prop_schema,
                            root_schema=root_schema,
                            path=_json_path_join(path, key),
                            _for_if_check=_for_if_check,
                        )
                    )

        additional = schema.get("additionalProperties", True)
        if additional is False and isinstance(properties, dict):
            allowed_keys = set(properties.keys())
            for key in instance.keys():
                if key not in allowed_keys:
                    errors.append(f"{path}: additional property {key!r} not allowed")
        elif isinstance(additional, dict) and isinstance(properties, dict):
            allowed_keys = set(properties.keys())
            for key, value in instance.items():
                if key in allowed_keys:
                    continue
                errors.extend(
                    validate_jsonschema(
                        value,
                        additional,
                        root_schema=root_schema,
                        path=_json_path_join(path, key),
                        _for_if_check=_for_if_check,
                    )
                )

    if not _for_if_check and errors:
        return errors
    return errors


def jsonlogic_vars(expr: Any) -> set[str]:
    out: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if len(node) == 1 and "var" in node:
                var_val = node["var"]
                if isinstance(var_val, str):
                    out.add(var_val)
                elif isinstance(var_val, list) and var_val and isinstance(var_val[0], str):
                    out.add(var_val[0])
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(expr)
    return out


def jsonlogic_apply(expr: Any, data: dict[str, Any]) -> Any:
    if isinstance(expr, dict):
        if len(expr) != 1:
            return {k: jsonlogic_apply(v, data) for k, v in expr.items()}
        op, arg = next(iter(expr.items()))
        if op == "var":
            if isinstance(arg, str):
                return get_path(data, arg)
            if isinstance(arg, list) and arg:
                default = arg[1] if len(arg) > 1 else None
                val = get_path(data, arg[0]) if isinstance(arg[0], str) else None
                return default if val is None else val
            return None
        if op in ("==", "!=", "<", "<=", ">", ">="):
            if not isinstance(arg, list) or len(arg) != 2:
                return False
            left = jsonlogic_apply(arg[0], data)
            right = jsonlogic_apply(arg[1], data)
            if op == "==":
                return left == right
            if op == "!=":
                return left != right
            if left is None or right is None:
                return False
            try:
                if op == "<":
                    return left < right
                if op == "<=":
                    return left <= right
                if op == ">":
                    return left > right
                if op == ">=":
                    return left >= right
            except TypeError:
                return False
            return False
        if op == "and":
            if not isinstance(arg, list):
                return False
            for part in arg:
                if not truthy(jsonlogic_apply(part, data)):
                    return False
            return True
        if op == "or":
            if not isinstance(arg, list):
                return False
            for part in arg:
                if truthy(jsonlogic_apply(part, data)):
                    return True
            return False
        if op == "!":
            return not truthy(jsonlogic_apply(arg, data))
        if op == "in":
            if not isinstance(arg, list) or len(arg) != 2:
                return False
            item = jsonlogic_apply(arg[0], data)
            arr = jsonlogic_apply(arg[1], data)
            if not isinstance(arr, list):
                return False
            return item in arr
        raise ValidationError(f"Unsupported JSONLogic op: {op}")
    if isinstance(expr, list):
        return [jsonlogic_apply(v, data) for v in expr]
    return expr


def truthy(value: Any) -> bool:
    return bool(value)


def get_path(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def safe_div(numerator: Any, denominator: Any) -> float | None:
    if numerator is None or denominator is None:
        return None
    if not _is_number(numerator) or not _is_number(denominator):
        return None
    if float(denominator) == 0.0:
        return None
    out = float(numerator) / float(denominator)
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


_PLACEHOLDER_RE = re.compile(r"\{[a-z0-9_]+\}")


def extract_placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(text))


def format_value(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "true" if value else "false"
    if _is_integer(value):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(format_value(v) for v in value)
    return str(value)


def render_template(text: str, values: dict[str, Any]) -> str:
    used = extract_placeholders(text)
    rendered = text
    for ph in used:
        key = ph.strip("{}")
        rendered = rendered.replace(ph, format_value(values.get(key)))
    return rendered


def is_always_true_jsonlogic(expr: Any) -> bool:
    return isinstance(expr, dict) and expr == {"==": [1, 1]}


@dataclass(frozen=True)
class SelectedRule:
    rule: dict[str, Any]
    matched: bool
    used_fallback: bool


def select_rule(
    rules: list[dict[str, Any]],
    *,
    context: dict[str, Any],
) -> SelectedRule | None:
    matching: list[dict[str, Any]] = []
    fallback: list[dict[str, Any]] = []
    for rule in rules:
        when = rule.get("when")
        if is_always_true_jsonlogic(when):
            fallback.append(rule)
            continue
        try:
            if truthy(jsonlogic_apply(when, context)):
                matching.append(rule)
        except ValidationError:
            continue
    if matching:
        chosen = max(matching, key=lambda r: int(r.get("priority", 0)))
        return SelectedRule(rule=chosen, matched=True, used_fallback=False)
    if fallback:
        chosen = max(fallback, key=lambda r: int(r.get("priority", 0)))
        return SelectedRule(rule=chosen, matched=True, used_fallback=True)
    return None

