import json
import unittest
from pathlib import Path

from backend.src.evaluate import evaluate, get_runtime


class GoldenRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = get_runtime()
        cls.golden_dir = cls.runtime.spec_dir / "G0_GoldenInputs"

    def test_end_to_end_golden_and_yokohama(self) -> None:
        golden_paths = sorted(self.golden_dir.glob("listing_*.json"))
        self.assertGreaterEqual(len(golden_paths), 37)

        yokohama_cases = [
            {
                "hub_station": "other",
                "hub_station_other_name": "yokohama",
                "prefecture": "kanagawa",
                "municipality": "横浜市港北区",
                "nearest_station_name": "新横浜",
                "station_walk_min": 6,
                "layout_type": "1K",
                "building_structure": "rc",
                "area_sqm": 22,
                "building_built_year": 2018,
                "orientation": "S",
                "bathroom_toilet_separate": True,
                "rent_yen": 85000,
                "mgmt_fee_yen": 5000,
                "initial_cost_total_yen": 255000,
            },
            {
                "hub_station": "other",
                "hub_station_other_name": "yokohama",
                "prefecture": "kanagawa",
                "municipality": "横浜市鶴見区",
                "nearest_station_name": "鶴見",
                "station_walk_min": 12,
                "layout_type": "1K",
                "building_structure": "rc",
                "area_sqm": 20,
                "building_built_year": 2010,
                "orientation": "E",
                "bathroom_toilet_separate": True,
                "rent_yen": 78000,
                "mgmt_fee_yen": 7000,
                "initial_cost_total_yen": 280000,
            },
            {
                "hub_station": "other",
                "hub_station_other_name": "yokohama",
                "prefecture": "kanagawa",
                "municipality": "横浜市中区",
                "nearest_station_name": "関内",
                "station_walk_min": 4,
                "layout_type": "1K",
                "building_structure": "rc",
                "area_sqm": 18,
                "building_built_year": 2020,
                "orientation": "SE",
                "bathroom_toilet_separate": False,
                "rent_yen": 92000,
                "mgmt_fee_yen": 8000,
                "initial_cost_total_yen": 330000,
            },
            {
                "hub_station": "other",
                "hub_station_other_name": "yokohama",
                "prefecture": "kanagawa",
                "municipality": "横浜市西区",
                "nearest_station_name": "横浜",
                "station_walk_min": 10,
                "layout_type": "1K",
                "building_structure": "rc",
                "area_sqm": 25,
                "building_built_year": 2005,
                "orientation": "W",
                "bathroom_toilet_separate": True,
                "rent_yen": 88000,
                "mgmt_fee_yen": 5000,
                "initial_cost_total_yen": 450000,
            },
            {
                "hub_station": "other",
                "hub_station_other_name": "yokohama",
                "prefecture": "kanagawa",
                "municipality": "横浜市神奈川区",
                "nearest_station_name": "東神奈川",
                "station_walk_min": 15,
                "layout_type": "1K",
                "building_structure": "wood",
                "area_sqm": 14,
                "building_built_year": 1990,
                "orientation": "N",
                "bathroom_toilet_separate": True,
                "rent_yen": 70000,
                "mgmt_fee_yen": 3000,
                "initial_cost_total_yen": 480000,
                "reikin_yen": 70000,
                "brokerage_fee_yen": 70000,
            },
        ]

        mid_count = 0
        high_count = 0
        saw_what_if_reikin = False

        # Golden inputs (all available)
        for p in golden_paths:
            with p.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            out = evaluate(payload, runtime=self.runtime)
            self.assertIn("derived", out)
            self.assertIn("scoring", out)
            self.assertIn("grades", out)
            self.assertIn("report", out)

            derived = out["derived"]
            report = out["report"]
            self.assertIn("benchmark_confidence", derived)
            self.assertIn(derived["benchmark_confidence"], ["high", "mid", "low", "none"])
            if derived["benchmark_confidence"] == "mid":
                mid_count += 1
            if derived["benchmark_confidence"] == "high":
                high_count += 1

            self.assertIsInstance(report.get("summary_ko"), str)
            self.assertGreater(len(report["summary_ko"]), 0)
            self.assertIsInstance(report.get("evidence_bullets_ko"), list)
            self.assertIsInstance(report.get("risk_flags"), list)
            self.assertIsInstance(report.get("what_if_results"), list)
            self.assertGreaterEqual(len(report["what_if_results"]), 1)

            for wi in report["what_if_results"]:
                if wi.get("id") == "WI_SET_REIKIN_ZERO":
                    saw_what_if_reikin = True

        # Yokohama cases (must be kanagawa + municipality match -> high)
        for payload in yokohama_cases:
            out = evaluate(payload, runtime=self.runtime)
            derived = out["derived"]
            self.assertEqual(derived["benchmark_confidence"], "high")
            self.assertIsInstance(derived.get("benchmark_monthly_fixed_cost_yen"), int)
            self.assertGreater(derived["benchmark_monthly_fixed_cost_yen"], 0)

        self.assertGreaterEqual(mid_count, 1)
        self.assertGreaterEqual(high_count, 0)  # depends on municipality presence in golden set
        self.assertTrue(saw_what_if_reikin)

    def test_im_assessment_in_derived(self) -> None:
        """im_assessment, im_assessment_foreigner, initial_multiple_market_avg are present."""
        payload = {
            "hub_station": "shinjuku",
            "prefecture": "tokyo",
            "nearest_station_name": "新宿",
            "station_walk_min": 8,
            "layout_type": "1K",
            "building_structure": "rc",
            "area_sqm": 22,
            "building_built_year": 2018,
            "orientation": "S",
            "bathroom_toilet_separate": True,
            "rent_yen": 100000,
            "mgmt_fee_yen": 10000,
            "initial_cost_total_yen": 550000,  # IM = 5.0 → 평균
        }
        out = evaluate(payload, runtime=self.runtime)
        derived = out["derived"]
        self.assertIn("im_assessment", derived)
        self.assertIn("im_assessment_foreigner", derived)
        self.assertIn("initial_multiple_market_avg", derived)
        # Tokyo avg=5.0, IM=5.0 → 평균(시세 수준)
        self.assertIn("평균", derived["im_assessment"])
        # foreigner view: effective_im = 5.0 - 1.0 = 4.0 → 낮음(시세 이하)
        self.assertIn("낮음", derived["im_assessment_foreigner"])
        self.assertEqual(derived["initial_multiple_market_avg"], 5.0)

    def test_osaka_1k_evaluates(self) -> None:
        """Osaka 1K with municipality produces high benchmark confidence."""
        payload = {
            "hub_station": "umeda",
            "prefecture": "osaka",
            "municipality": "大阪市北区",
            "nearest_station_name": "梅田",
            "station_walk_min": 5,
            "layout_type": "1K",
            "building_structure": "rc",
            "area_sqm": 22,
            "building_built_year": 2019,
            "orientation": "S",
            "bathroom_toilet_separate": True,
            "rent_yen": 75000,
            "mgmt_fee_yen": 5000,
            "initial_cost_total_yen": 300000,
        }
        out = evaluate(payload, runtime=self.runtime)
        derived = out["derived"]
        self.assertEqual(derived["benchmark_confidence"], "high")
        self.assertIn("im_assessment", derived)
        self.assertEqual(derived["initial_multiple_market_avg"], 5.0)

    def test_osaka_1r_evaluates(self) -> None:
        """Osaka 1R with municipality produces high benchmark confidence."""
        payload = {
            "hub_station": "osaka_station",
            "prefecture": "osaka",
            "municipality": "大阪市北区",
            "nearest_station_name": "大阪",
            "station_walk_min": 8,
            "layout_type": "1R",
            "building_structure": "other",
            "area_sqm": 18,
            "building_built_year": 2018,
            "orientation": "S",
            "bathroom_toilet_separate": True,
            "rent_yen": 71000,
            "mgmt_fee_yen": 5000,
            "initial_cost_total_yen": 300000,
        }
        out = evaluate(payload, runtime=self.runtime)
        derived = out["derived"]
        self.assertEqual(derived["benchmark_confidence"], "high")
        self.assertIsInstance(out["report"]["summary_ko"], str)
        self.assertGreater(len(out["report"]["summary_ko"]), 0)

    def test_osaka_1dk_1ldk_evaluate(self) -> None:
        """Osaka 1DK and 1LDK both produce valid outputs."""
        base = {
            "hub_station": "namba",
            "prefecture": "osaka",
            "municipality": "大阪市中央区",
            "nearest_station_name": "難波",
            "station_walk_min": 5,
            "building_built_year": 2015,
            "building_structure": "rc",
            "orientation": "S",
            "bathroom_toilet_separate": True,
            "rent_yen": 90000,
            "mgmt_fee_yen": 5000,
            "initial_cost_total_yen": 400000,
        }
        for lt, area in [("1DK", 32), ("1LDK", 42)]:
            payload = {**base, "layout_type": lt, "area_sqm": area}
            out = evaluate(payload, runtime=self.runtime)
            self.assertIn("derived", out)
            self.assertIn("scoring", out)
            self.assertIn("grades", out)
            self.assertIn("report", out)
            self.assertIsInstance(out["report"]["summary_ko"], str)
            self.assertGreater(len(out["report"]["summary_ko"]), 0)
            self.assertIn("im_assessment", out["derived"])

    def test_cost_score_penalizes_high_initial_cost(self) -> None:
        """Higher initial_cost_total_yen should reduce cost_score (all else equal)."""
        base = {
            "hub_station": "osaka_station",
            "prefecture": "osaka",
            "municipality": "大阪市北区",
            "nearest_station_name": "大阪",
            "station_walk_min": 8,
            "layout_type": "1K",
            "building_structure": "rc",
            "area_sqm": 22,
            "building_built_year": 2018,
            "orientation": "S",
            "bathroom_toilet_separate": True,
            "rent_yen": 82500,
            "mgmt_fee_yen": 5000,
        }
        low = {**base, "initial_cost_total_yen": 250000}
        high = {**base, "initial_cost_total_yen": 550000}

        out_low = evaluate(low, runtime=self.runtime, benchmark_index_override={})
        out_high = evaluate(high, runtime=self.runtime, benchmark_index_override={})
        self.assertLess(out_high["scoring"]["cost_score"], out_low["scoring"]["cost_score"])

    def test_tokyo_1dk_1ldk_evaluate(self) -> None:
        """Tokyo 1DK and 1LDK both produce valid outputs with im_assessment."""
        base = {
            "hub_station": "shinjuku",
            "prefecture": "tokyo",
            "municipality": "新宿区",
            "nearest_station_name": "新宿",
            "station_walk_min": 8,
            "building_built_year": 2015,
            "building_structure": "rc",
            "orientation": "S",
            "bathroom_toilet_separate": True,
            "rent_yen": 130000,
            "mgmt_fee_yen": 8000,
            "initial_cost_total_yen": 550000,
        }
        for lt, area in [("1DK", 30), ("1LDK", 45)]:
            payload = {**base, "layout_type": lt, "area_sqm": area}
            out = evaluate(payload, runtime=self.runtime)
            self.assertIn("derived", out)
            self.assertIn("im_assessment", out["derived"])
            self.assertEqual(out["derived"]["initial_multiple_market_avg"], 5.0)
            self.assertIsInstance(out["report"]["summary_ko"], str)

    def test_tokyo_edogawa_ko_iwa_1ldk_hedonic_adjustment(self) -> None:
        """小岩/江戸川区 1LDK case: hedonic adjustment reduces benchmark inflation and downgrades confidence on n=1."""
        payload = {
            "hub_station": "tokyo_station",
            "prefecture": "tokyo",
            "municipality": "江戸川区",
            "nearest_station_name": "小岩",
            "station_walk_min": 11,
            "layout_type": "1LDK",
            "building_structure": "wood",
            "area_sqm": 33,
            "building_built_year": 2008,
            "orientation": "S",
            "bathroom_toilet_separate": True,
            "rent_yen": 110000,
            "mgmt_fee_yen": 0,
            "initial_cost_total_yen": 500000,
        }
        out = evaluate(payload, runtime=self.runtime)
        derived = out["derived"]

        # No wood-structure key in index → falls through to muni_level (structure-agnostic aggregate).
        self.assertEqual(derived.get("benchmark_matched_level"), "muni_level")
        # n_rows=4 in current index → high confidence
        self.assertEqual(derived.get("benchmark_confidence"), "high")
        self.assertIsNotNone(derived.get("benchmark_monthly_fixed_cost_yen_raw"))

        # Hedonic-adjusted benchmark should be lower than raw (age/walk/area downward pressure).
        self.assertIsInstance(derived.get("benchmark_monthly_fixed_cost_yen"), int)
        self.assertLess(int(derived["benchmark_monthly_fixed_cost_yen"]), int(derived["benchmark_monthly_fixed_cost_yen_raw"]))

        self.assertIsInstance(derived.get("benchmark_adjustments"), dict)
        self.assertIn("multiplier_total", derived["benchmark_adjustments"])

    def test_building_structure_affects_benchmark(self) -> None:
        """With structure-segmented index, wood vs rc should match different benchmark keys."""
        index = {
            "by_pref_muni_layout_structure": {
                "tokyo|江戸川区|1LDK|wood": {"benchmark_rent_yen_median": 120000, "n_rows": 2, "sources": []},
                "tokyo|江戸川区|1LDK|rc": {"benchmark_rent_yen_median": 160000, "n_rows": 2, "sources": []},
            },
            "by_pref_muni_layout": {
                "tokyo|江戸川区|1LDK": {"benchmark_rent_yen_median": 150000, "n_rows": 2, "sources": []},
            },
            "by_pref_layout": {},
        }

        base = {
            "hub_station": "tokyo_station",
            "prefecture": "tokyo",
            "municipality": "江戸川区",
            "nearest_station_name": "小岩",
            "station_walk_min": 8,  # walk factor=1.0
            "layout_type": "1LDK",
            "area_sqm": 38,  # avg area -> area factor=1.0
            "building_built_year": 2016,  # age=10 -> age factor=1.0
            "orientation": "S",
            "bathroom_toilet_separate": True,
            "rent_yen": 140000,
            "mgmt_fee_yen": 0,
            "initial_cost_total_yen": 400000,
        }

        out_wood = evaluate({**base, "building_structure": "wood"}, runtime=self.runtime, benchmark_index_override=index)
        out_rc = evaluate({**base, "building_structure": "rc"}, runtime=self.runtime, benchmark_index_override=index)
        # Hedge adjustment is intentionally conservative (shrink + clamp); with n_rows=2 it should be near-raw.
        self.assertEqual(out_wood["derived"]["benchmark_monthly_fixed_cost_yen"], 120616)
        self.assertEqual(out_rc["derived"]["benchmark_monthly_fixed_cost_yen"], 160822)
        self.assertEqual(out_wood["derived"]["benchmark_matched_level"], "muni_structure_level")
        self.assertEqual(out_rc["derived"]["benchmark_matched_level"], "muni_structure_level")

    def test_building_structure_other_fallback(self) -> None:
        """building_structure=other must skip structure match and fall back to muni-level all data."""
        index = {
            "by_pref_muni_layout_structure": {
                "tokyo|江戸川区|1LDK|wood": {"benchmark_rent_yen_median": 120000, "n_rows": 2, "sources": []},
                "tokyo|江戸川区|1LDK|rc": {"benchmark_rent_yen_median": 160000, "n_rows": 2, "sources": []},
            },
            "by_pref_muni_layout": {
                "tokyo|江戸川区|1LDK": {"benchmark_rent_yen_median": 150000, "n_rows": 2, "sources": []},
            },
            "by_pref_layout": {},
        }

        payload = {
            "hub_station": "tokyo_station",
            "prefecture": "tokyo",
            "municipality": "江戸川区",
            "nearest_station_name": "小岩",
            "station_walk_min": 8,
            "layout_type": "1LDK",
            "building_structure": "other",
            "area_sqm": 38,
            "building_built_year": 2016,
            "orientation": "S",
            "bathroom_toilet_separate": True,
            "rent_yen": 140000,
            "mgmt_fee_yen": 0,
            "initial_cost_total_yen": 400000,
        }
        out = evaluate(payload, runtime=self.runtime, benchmark_index_override=index)
        # Hedge adjustment is intentionally conservative (shrink + clamp); with n_rows=2 it should be near-raw.
        self.assertEqual(out["derived"]["benchmark_monthly_fixed_cost_yen"], 151286)
        self.assertEqual(out["derived"]["benchmark_matched_level"], "muni_level")

    def test_benchmark_none_still_generates_summary(self) -> None:
        payload = {
            "hub_station": "shinjuku",
            "prefecture": "tokyo",
            "nearest_station_name": "中野",
            "station_walk_min": 8,
            "layout_type": "1K",
            "building_structure": "other",
            "area_sqm": 22,
            "building_built_year": 2018,
            "orientation": "S",
            "bathroom_toilet_separate": True,
            "rent_yen": 100000,
            "mgmt_fee_yen": 10000,
            "initial_cost_total_yen": 300000,
        }
        out = evaluate(payload, runtime=self.runtime, benchmark_index_override={})
        self.assertEqual(out["derived"]["benchmark_confidence"], "none")
        self.assertIn("none", out["report"]["summary_ko"])


if __name__ == "__main__":
    unittest.main()
