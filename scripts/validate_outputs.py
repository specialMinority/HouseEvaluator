from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from _lib import ValidationError, jsonlogic_vars, load_json, load_yaml, validate_jsonschema


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    path: str | None = None


def _add(finds: list[Finding], code: str, message: str, path: str | None = None) -> None:
    finds.append(Finding(code=code, message=message, path=path))


def _validate_json_schema_pair(
    *, label: str, instance_path: Path, schema_path: Path, findings: list[Finding]
) -> dict[str, Any] | None:
    try:
        instance = load_json(instance_path)
    except Exception as e:
        _add(findings, "JSON_PARSE", f"{label}: failed to parse JSON: {e}", str(instance_path))
        return None

    try:
        schema = load_json(schema_path)
    except Exception as e:
        _add(findings, "SCHEMA_PARSE", f"{label}: failed to parse schema JSON: {e}", str(schema_path))
        return None

    errors = validate_jsonschema(instance, schema)
    for err in errors:
        _add(findings, "SCHEMA_FAIL", f"{label}: {err}", str(instance_path))
    return instance


def _v0_sets(v0: dict[str, Any]) -> dict[str, set[str]]:
    enums = v0.get("enums") or {}
    return {
        "prefecture": set(enums.get("prefecture") or []),
        "layout_type": set(enums.get("layout_type") or []),
        "hub_station": set(enums.get("hub_station") or []),
        "orientation": set(enums.get("orientation") or []),
        "grade": set(enums.get("grade") or []),
        "severity": set(enums.get("severity") or []),
        "benchmark_confidence": set(enums.get("benchmark_confidence") or []),
        "risk_flag_ids": {rf.get("id") for rf in (v0.get("risk_flags") or []) if isinstance(rf, dict)},
        "input_keys": set((v0.get("keys") or {}).get("input", {}).keys()),
        "derived_keys": set((v0.get("keys") or {}).get("derived", {}).keys()),
        "scoring_keys": set((v0.get("keys") or {}).get("scoring", {}).keys()),
        "grade_keys": set((v0.get("keys") or {}).get("grades", {}).keys()),
        "report_placeholders": {p.strip("{}") for p in (v0.get("report_placeholders") or []) if isinstance(p, str)},
    }


def _validate_when_is_object(
    *, label: str, rules: list[dict[str, Any]], where: str, findings: list[Finding]
) -> None:
    for idx, rule in enumerate(rules):
        when = rule.get(where)
        if not isinstance(when, dict):
            _add(
                findings,
                "WHEN_NOT_OBJECT",
                f"{label}: {where} must be an object (JSONLogic). strings are forbidden.",
                f"{label}{where}[{idx}]",
            )


def _validate_s1_against_v0(s1: dict[str, Any], v0_sets: dict[str, set[str]], findings: list[Finding]) -> None:
    fields = s1.get("fields")
    if not isinstance(fields, list):
        _add(findings, "S1_FIELDS", "S1.fields must be an array", "S1.fields")
        return

    for idx, f in enumerate(fields):
        if not isinstance(f, dict):
            _add(findings, "S1_FIELD_TYPE", "S1.fields item must be an object", f"S1.fields[{idx}]")
            continue
        key = f.get("key")
        if isinstance(key, str) and key not in v0_sets["input_keys"]:
            _add(findings, "V0_KEY", f"S1 field key {key!r} not in V0.keys.input", f"S1.fields[{idx}].key")
        if key == "qa_notes":
            _add(
                findings,
                "QA_NOTES",
                "S1.qa_notes must be split into a separate QA file (not in contract JSON).",
                f"S1.fields[{idx}]",
            )

    required = s1.get("mvp_required_fields")
    if isinstance(required, list):
        missing = [k for k in required if k not in v0_sets["input_keys"]]
        if missing:
            _add(findings, "V0_KEY", f"S1.mvp_required_fields contains unknown keys: {missing}", "S1.mvp_required_fields")


def _validate_s2_against_v0(
    s2: dict[str, Any], s1: dict[str, Any], v0_sets: dict[str, set[str]], findings: list[Finding]
) -> None:
    s1_keys = {f.get("key") for f in (s1.get("fields") or []) if isinstance(f, dict) and isinstance(f.get("key"), str)}
    allowed_input_refs = s1_keys | v0_sets["derived_keys"]

    features = s2.get("features") or []
    if not isinstance(features, list):
        _add(findings, "S2_FEATURES", "S2.features must be an array", "S2.features")
    else:
        for idx, feat in enumerate(features):
            if not isinstance(feat, dict):
                continue
            key = feat.get("input_key")
            if isinstance(key, str) and key not in allowed_input_refs:
                _add(
                    findings,
                    "INPUT_KEY_UNKNOWN",
                    f"S2.features[{idx}].input_key {key!r} not found in S1 fields nor V0 derived keys",
                    f"S2.features[{idx}].input_key",
                )

    _validate_when_is_object(label="S2.risk_flag_rules", rules=s2.get("risk_flag_rules") or [], where="when", findings=findings)
    _validate_when_is_object(label="S2.tradeoff_rules", rules=s2.get("tradeoff_rules") or [], where="when", findings=findings)

    for idx, rule in enumerate(s2.get("risk_flag_rules") or []):
        if not isinstance(rule, dict):
            continue
        outputs = rule.get("outputs") or {}
        if not isinstance(outputs, dict):
            continue
        rf_id = outputs.get("risk_flag_id")
        if isinstance(rf_id, str) and rf_id not in v0_sets["risk_flag_ids"]:
            _add(findings, "RISK_FLAG_ID", f"S2 risk_flag_id {rf_id!r} not in V0 risk_flags", f"S2.risk_flag_rules[{idx}]")
        sev = outputs.get("severity")
        if isinstance(sev, str) and sev not in v0_sets["severity"]:
            _add(findings, "SEVERITY", f"S2 severity {sev!r} not in V0.enums.severity", f"S2.risk_flag_rules[{idx}]")

    bands = (s2.get("grade_thresholds") or {}).get("overall")
    if isinstance(bands, list):
        for i, b in enumerate(bands):
            if not isinstance(b, dict):
                continue
            g = b.get("grade")
            if isinstance(g, str) and g not in v0_sets["grade"]:
                _add(findings, "GRADE_ENUM", f"S2 grade {g!r} not in V0.enums.grade", f"S2.grade_thresholds.overall[{i}]")

    whatifs = s2.get("what_if_rules") or []
    if isinstance(whatifs, list):
        for idx, w in enumerate(whatifs):
            if not isinstance(w, dict):
                continue
            enabled_if = w.get("enabled_if")
            if enabled_if is not None and not isinstance(enabled_if, dict):
                _add(
                    findings,
                    "WHEN_NOT_OBJECT",
                    "S2.what_if_rules[].enabled_if must be an object (JSONLogic). strings are forbidden.",
                    f"S2.what_if_rules[{idx}].enabled_if",
                )


def _validate_d1_against_v0(d1: dict[str, Any], v0_sets: dict[str, set[str]], findings: list[Finding]) -> None:
    cov = d1.get("coverage") or {}
    if isinstance(cov, dict):
        for key, allowed in (
            ("prefectures", v0_sets["prefecture"]),
            ("layout_types", v0_sets["layout_type"]),
            ("hub_stations", v0_sets["hub_station"]),
        ):
            vals = cov.get(key)
            if isinstance(vals, list):
                bad = [v for v in vals if v not in allowed]
                if bad:
                    _add(findings, "ENUM", f"D1.coverage.{key} has invalid tokens: {bad}", f"D1.coverage.{key}")

    for idx, fb in enumerate(d1.get("fallback_policy") or []):
        if not isinstance(fb, dict):
            continue
        bc = fb.get("benchmark_confidence")
        if isinstance(bc, str) and bc not in v0_sets["benchmark_confidence"]:
            _add(
                findings,
                "ENUM",
                f"D1.fallback_policy[{idx}].benchmark_confidence {bc!r} not in V0.enums.benchmark_confidence",
                f"D1.fallback_policy[{idx}]",
            )


def _validate_c1_against_v0(c1: dict[str, Any], v0_sets: dict[str, set[str]], findings: list[Finding]) -> None:
    placeholders = c1.get("placeholders") or []
    if isinstance(placeholders, list):
        bad = []
        for p in placeholders:
            if not isinstance(p, str) or not p.startswith("{") or not p.endswith("}"):
                continue
            token = p.strip("{}")
            if token not in v0_sets["report_placeholders"]:
                bad.append(p)
        if bad:
            _add(findings, "PLACEHOLDER_V0", f"C1.placeholders contains tokens not allowed by V0: {bad}", "C1.placeholders")

    rules = c1.get("rules") or []
    if not isinstance(rules, list):
        _add(findings, "C1_RULES", "C1.rules must be an array", "C1.rules")
        return

    _validate_when_is_object(label="C1.rules", rules=rules, where="when", findings=findings)

    for idx, r in enumerate(rules):
        if not isinstance(r, dict):
            continue
        rf_expl = r.get("risk_flag_explanations_ko")
        if isinstance(rf_expl, dict):
            for rf_id in rf_expl.keys():
                if isinstance(rf_id, str) and rf_id not in v0_sets["risk_flag_ids"]:
                    _add(
                        findings,
                        "RISK_FLAG_ID",
                        f"C1 risk_flag_explanations_ko has unknown risk_flag_id {rf_id!r}",
                        f"C1.rules[{idx}].risk_flag_explanations_ko",
                    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate agent outputs against K0 contracts + V0 vocabulary.")
    parser.add_argument("--root", type=Path, default=Path("."), help="Repo root (default: .)")
    args = parser.parse_args()

    root: Path = args.root
    findings: list[Finding] = []

    v0_path = root / "V0_Vocabulary.yml"
    try:
        v0 = load_yaml(v0_path)
    except Exception as e:
        _add(findings, "V0_PARSE", f"Failed to parse V0_Vocabulary.yml: {e}", str(v0_path))
        v0 = {}

    v0_sets = _v0_sets(v0) if isinstance(v0, dict) else _v0_sets({})

    s1 = _validate_json_schema_pair(
        label="S1",
        instance_path=root / "agents/agent_B_inputschema/out/S1_InputSchema.json",
        schema_path=root / "K0_OutputContracts/S1_InputSchema.schema.json",
        findings=findings,
    )
    s2 = _validate_json_schema_pair(
        label="S2",
        instance_path=root / "agents/agent_C_scoring/out/S2_ScoringSpec.json",
        schema_path=root / "K0_OutputContracts/S2_ScoringSpec.schema.json",
        findings=findings,
    )
    d1 = _validate_json_schema_pair(
        label="D1",
        instance_path=root / "agents/agent_D_benchmark/out/D1_BenchmarkSpec.json",
        schema_path=root / "K0_OutputContracts/D1_BenchmarkSpec.schema.json",
        findings=findings,
    )
    c1 = _validate_json_schema_pair(
        label="C1",
        instance_path=root / "agents/agent_E_copy/out/C1_ReportTemplates.json",
        schema_path=root / "K0_OutputContracts/C1_ReportTemplates.schema.json",
        findings=findings,
    )

    if isinstance(s1, dict):
        _validate_s1_against_v0(s1, v0_sets, findings)
    if isinstance(s2, dict) and isinstance(s1, dict):
        _validate_s2_against_v0(s2, s1, v0_sets, findings)
    if isinstance(d1, dict):
        _validate_d1_against_v0(d1, v0_sets, findings)
    if isinstance(c1, dict):
        _validate_c1_against_v0(c1, v0_sets, findings)

    error_count = len(findings)
    if error_count == 0:
        print("PASS validate_outputs")
        return 0

    print(f"FAIL validate_outputs ({error_count} findings)")
    for f in findings:
        loc = f" [{f.path}]" if f.path else ""
        print(f"- {f.code}{loc}: {f.message}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
