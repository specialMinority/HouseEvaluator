import unittest

from backend.src.evaluate import evaluate, get_runtime


class CostModelPR4Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = get_runtime()

    def test_monthly_extras_and_move_in_total(self) -> None:
        payload = {
            "hub_station": "shinjuku",
            "prefecture": "tokyo",
            "municipality": "新宿区",
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
            "initial_cost_total_yen": 300000,
            "monthly_support_fee_yen": 1000,
            "monthly_other_fees_yen": 500,
        }

        out = evaluate(payload, runtime=self.runtime)
        d = out["derived"]

        self.assertEqual(d["monthly_base_cost_yen"], 110000)
        self.assertEqual(d["monthly_extra_cost_yen"], 1500)
        self.assertEqual(d["monthly_all_in_cost_yen"], 111500)

        self.assertEqual(d["initial_cost_contract_only_yen"], 300000)
        self.assertEqual(d["move_in_total_yen"], 411500)

        self.assertAlmostEqual(d["initial_multiple"], 300000 / 110000, places=6)
        self.assertAlmostEqual(d["initial_multiple_move_in_total"], 411500 / 110000, places=6)

        self.assertIsInstance(d.get("monthly_extra_costs"), dict)
        self.assertEqual(d["monthly_extra_costs"].get("monthly_support_fee_yen"), 1000)
        self.assertEqual(d["monthly_extra_costs"].get("monthly_other_fees_yen"), 500)

    def test_initial_cost_includes_first_month_rent_toggle(self) -> None:
        payload = {
            "hub_station": "shinjuku",
            "prefecture": "tokyo",
            "municipality": "新宿区",
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
            # contract=300k + first-month base=110k => 410k
            "initial_cost_total_yen": 410000,
            "initial_cost_includes_first_month_rent": True,
            "monthly_support_fee_yen": 1000,
            "monthly_other_fees_yen": 500,
        }

        out = evaluate(payload, runtime=self.runtime)
        d = out["derived"]

        self.assertTrue(d["initial_cost_includes_first_month_rent"])
        self.assertEqual(d["monthly_base_cost_yen"], 110000)
        self.assertEqual(d["monthly_extra_cost_yen"], 1500)
        self.assertEqual(d["monthly_all_in_cost_yen"], 111500)

        # contract-only should subtract base monthly (rent+mgmt) once.
        self.assertEqual(d["initial_cost_contract_only_yen"], 300000)
        # move-in total should include base + extras once.
        self.assertEqual(d["move_in_total_yen"], 411500)
