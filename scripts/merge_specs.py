from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from _lib import extract_placeholders, jsonlogic_vars, load_json, load_yaml, validate_jsonschema


@dataclass(frozen=True)
class CoherenceIssue:
    severity: str  # "error" | "warn"
    code: str
    message: str


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_bundle_sources(root: Path) -> dict[str, Any]:
    s1 = load_json(root / "agents/agent_B_inputschema/out/S1_InputSchema.json")
    s2 = load_json(root / "agents/agent_C_scoring/out/S2_ScoringSpec.json")
    d1 = load_json(root / "agents/agent_D_benchmark/out/D1_BenchmarkSpec.json")
    c1 = load_json(root / "agents/agent_E_copy/out/C1_ReportTemplates.json")
    v0 = load_yaml(root / "V0_Vocabulary.yml")
    return {"S1": s1, "S2": s2, "D1": d1, "C1": c1, "V0": v0}


def _validate_k0(root: Path, data: dict[str, Any]) -> list[CoherenceIssue]:
    issues: list[CoherenceIssue] = []
    pairs = [
        ("S1", "K0_OutputContracts/S1_InputSchema.schema.json", data["S1"]),
        ("S2", "K0_OutputContracts/S2_ScoringSpec.schema.json", data["S2"]),
        ("D1", "K0_OutputContracts/D1_BenchmarkSpec.schema.json", data["D1"]),
        ("C1", "K0_OutputContracts/C1_ReportTemplates.schema.json", data["C1"]),
    ]
    for label, schema_rel, instance in pairs:
        schema = load_json(root / schema_rel)
        errs = validate_jsonschema(instance, schema)
        for e in errs:
            issues.append(CoherenceIssue("error", "K0_SCHEMA", f"{label}: {e}"))
    return issues


def _coherence_checks(data: dict[str, Any]) -> list[CoherenceIssue]:
    issues: list[CoherenceIssue] = []

    v0 = data.get("V0") or {}
    v0_input_keys = set(((v0.get("keys") or {}).get("input") or {}).keys())
    v0_derived_keys = set(((v0.get("keys") or {}).get("derived") or {}).keys())
    v0_scoring_keys = set(((v0.get("keys") or {}).get("scoring") or {}).keys())
    v0_grade_keys = set(((v0.get("keys") or {}).get("grades") or {}).keys())
    v0_report_placeholders = {p.strip("{}") for p in (v0.get("report_placeholders") or []) if isinstance(p, str)}
    v0_risk_flag_ids = {rf.get("id") for rf in (v0.get("risk_flags") or []) if isinstance(rf, dict)}

    s1_field_keys = {
        f.get("key") for f in (data["S1"].get("fields") or []) if isinstance(f, dict) and isinstance(f.get("key"), str)
    }
    allowed_vars = v0_input_keys | v0_derived_keys | v0_scoring_keys | v0_grade_keys | s1_field_keys

    # S2 feature input_key -> must exist in S1 or V0 derived.
    for idx, feat in enumerate(data["S2"].get("features") or []):
        if not isinstance(feat, dict):
            continue
        k = feat.get("input_key")
        if isinstance(k, str) and k not in (s1_field_keys | v0_derived_keys):
            issues.append(
                CoherenceIssue(
                    "error",
                    "S2_INPUT_KEY",
                    f"S2.features[{idx}].input_key {k!r} not found in S1 fields nor V0 derived keys",
                )
            )

    # JSONLogic var usage must be in allowed context.
    for section in ("risk_flag_rules", "tradeoff_rules", "what_if_rules"):
        for idx, rule in enumerate(data["S2"].get(section) or []):
            if not isinstance(rule, dict):
                continue
            when = rule.get("when") if section != "what_if_rules" else rule.get("enabled_if")
            if when is None:
                continue
            if not isinstance(when, dict):
                issues.append(CoherenceIssue("error", "WHEN_NOT_OBJECT", f"S2.{section}[{idx}].when must be object"))
                continue
            for var in jsonlogic_vars(when):
                if var not in allowed_vars:
                    issues.append(
                        CoherenceIssue(
                            "error",
                            "JSONLOGIC_VAR",
                            f"S2.{section}[{idx}] references unknown var {var!r} (not in S1/V0 context)",
                        )
                    )

    # Risk flag IDs coherence
    for idx, rule in enumerate(data["S2"].get("risk_flag_rules") or []):
        if not isinstance(rule, dict):
            continue
        outputs = rule.get("outputs") or {}
        if not isinstance(outputs, dict):
            continue
        rf_id = outputs.get("risk_flag_id")
        if isinstance(rf_id, str) and rf_id not in v0_risk_flag_ids:
            issues.append(
                CoherenceIssue(
                    "error",
                    "RISK_FLAG_ID",
                    f"S2.risk_flag_rules[{idx}].outputs.risk_flag_id {rf_id!r} not in V0 risk_flags",
                )
            )

    # C1 placeholders
    c1_declared = {p.strip("{}") for p in (data["C1"].get("placeholders") or []) if isinstance(p, str)}
    bad_declared = sorted(p for p in c1_declared if p not in v0_report_placeholders)
    if bad_declared:
        issues.append(
            CoherenceIssue("error", "C1_PLACEHOLDER_V0", f"C1.placeholders contains non-V0 tokens: {bad_declared}")
        )

    # C1 templates must only use declared placeholders
    for idx, rule in enumerate(data["C1"].get("rules") or []):
        if not isinstance(rule, dict):
            continue
        texts: list[str] = []
        for k in ("summary_ko", "summary_ja", "notes"):
            v = rule.get(k)
            if isinstance(v, str):
                texts.append(v)
        for k in ("evidence_bullets_ko", "negotiation_suggestions_ko", "negotiation_suggestions_ja", "alternative_search_queries_ja"):
            arr = rule.get(k)
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, str):
                        texts.append(item)
        used: set[str] = set()
        for t in texts:
            used |= {p.strip("{}") for p in extract_placeholders(t)}
        unknown = sorted(p for p in used if p not in c1_declared)
        if unknown:
            issues.append(
                CoherenceIssue(
                    "error",
                    "C1_PLACEHOLDER_UNDECLARED",
                    f"C1.rules[{idx}] uses placeholders not declared in C1.placeholders: {unknown}",
                )
            )

        when = rule.get("when")
        if not isinstance(when, dict):
            issues.append(CoherenceIssue("error", "WHEN_NOT_OBJECT", f"C1.rules[{idx}].when must be object"))
        else:
            for var in jsonlogic_vars(when):
                if var not in allowed_vars:
                    issues.append(
                        CoherenceIssue(
                            "warn",
                            "C1_JSONLOGIC_VAR",
                            f"C1.rules[{idx}] references var {var!r} not in V0 context (may be runtime-only)",
                        )
                    )

        rf_expl = rule.get("risk_flag_explanations_ko")
        if isinstance(rf_expl, dict):
            bad = sorted(k for k in rf_expl.keys() if isinstance(k, str) and k not in v0_risk_flag_ids)
            if bad:
                issues.append(
                    CoherenceIssue(
                        "error",
                        "RISK_FLAG_ID",
                        f"C1.rules[{idx}].risk_flag_explanations_ko has unknown IDs: {bad}",
                    )
                )

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Bundle S1/S2/D1/C1 into a single spec bundle with coherence checks.")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, default=Path("integrate/merged/spec_bundle.json"))
    args = parser.parse_args()

    root: Path = args.root
    sources = _load_bundle_sources(root)
    issues = _validate_k0(root, sources) + _coherence_checks(sources)

    bundle = {
        "version": sources["S1"].get("version") or sources["S2"].get("version") or "0.0.0",
        "generated_at": "2026-02-17",
        "S1": sources["S1"],
        "S2": sources["S2"],
        "D1": sources["D1"],
        "C1": sources["C1"],
    }

    _write_json(root / args.out, bundle)
    _write_json(root / "integrate/merged/coherence_report.json", {"issues": [i.__dict__ for i in issues]})

    errors = [i for i in issues if i.severity == "error"]
    if errors:
        print(f"FAIL merge_specs ({len(errors)} errors, {len(issues)} total issues)")
        for i in errors[:50]:
            print(f"- {i.code}: {i.message}")
        if len(errors) > 50:
            print(f"... and {len(errors) - 50} more")
        return 1

    warns = [i for i in issues if i.severity == "warn"]
    if warns:
        print(f"WARN merge_specs ({len(warns)} warnings)")
    else:
        print("PASS merge_specs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
