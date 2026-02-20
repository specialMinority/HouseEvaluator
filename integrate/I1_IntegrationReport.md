# Integration Report

## 2026-02-17
- Change: Add `kanagawa` (Yokohama) to coverage + prefecture enums.
- D1: Add `municipality` as a segmentation dimension + Yokohama ward-level note.
- Validation: `python scripts/validate_outputs.py` PASS
- Bundle: `python scripts/merge_specs.py` PASS (`integrate/merged/spec_bundle.json`, `integrate/merged/coherence_report.json`)
- Regression: `python scripts/run_golden_regression.py` PASS (`integrate/merged/golden_regression.json`)
- Issues: none
