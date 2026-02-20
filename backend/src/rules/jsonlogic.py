from __future__ import annotations

from typing import Any


class JsonLogicError(ValueError):
    pass


def _get_var(data: dict[str, Any], var_expr: Any) -> Any:
    if isinstance(var_expr, str):
        return data.get(var_expr)
    if isinstance(var_expr, list) and var_expr:
        key = var_expr[0]
        default = var_expr[1] if len(var_expr) > 1 else None
        if isinstance(key, str):
            return data.get(key, default)
    raise JsonLogicError(f"Unsupported var expression: {var_expr!r}")


def apply(rule: Any, data: dict[str, Any]) -> Any:
    """
    Minimal JSONLogic evaluator (only ops used by this repo specs):
    - var, and, in
    - ==, !=, >, >=, <, <=
    """
    if rule is None:
        return None

    if isinstance(rule, (bool, int, float, str)):
        return rule

    if isinstance(rule, list):
        return [apply(x, data) for x in rule]

    if not isinstance(rule, dict):
        raise JsonLogicError(f"Unsupported rule type: {type(rule).__name__}")

    if len(rule) != 1:
        raise JsonLogicError(f"JSONLogic object must have exactly 1 key: {rule!r}")

    op, raw_args = next(iter(rule.items()))

    if op == "var":
        return _get_var(data, raw_args)

    args = raw_args if isinstance(raw_args, list) else [raw_args]

    if op == "and":
        # JSONLogic 'and' returns first falsy, else last truthy.
        last: Any = True
        for a in args:
            last = apply(a, data)
            if not bool(last):
                return last
        return last

    if op == "in":
        if len(args) != 2:
            raise JsonLogicError("'in' expects 2 args")
        needle = apply(args[0], data)
        haystack = apply(args[1], data)
        if haystack is None:
            return False
        if isinstance(haystack, list):
            return needle in haystack
        if isinstance(haystack, str):
            return str(needle) in haystack
        return False

    if op in ("==", "!=", ">", ">=", "<", "<="):
        if len(args) != 2:
            raise JsonLogicError(f"'{op}' expects 2 args")
        left = apply(args[0], data)
        right = apply(args[1], data)

        if op == "==":
            return left == right
        if op == "!=":
            return left != right

        # Non-equality comparisons: treat None as non-comparable.
        if left is None or right is None:
            return False

        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right

    raise JsonLogicError(f"Unsupported op: {op!r}")

