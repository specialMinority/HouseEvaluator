import json
import os
import threading
import unittest
from http.client import HTTPConnection
from http.server import HTTPServer

from backend.src.server import _ApiHandler


class E2ESmokeTest(unittest.TestCase):
    def _request_json(self, conn: HTTPConnection, method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
            headers["Content-Length"] = str(len(data))
        conn.request(method, path, body=data, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        ct = res.getheader("Content-Type") or ""
        if "application/json" in ct:
            return res.status, json.loads(raw.decode("utf-8"))
        return res.status, raw.decode("utf-8", errors="replace")

    def test_static_and_api(self) -> None:
        # E2E smoke must be deterministic/offline. Disable live portal scraping which can
        # be slow/flaky in CI or sandboxed environments.
        prev_live = os.environ.get("SUUMO_LIVE")
        os.environ["SUUMO_LIVE"] = "0"
        httpd = HTTPServer(("127.0.0.1", 0), _ApiHandler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            conn = HTTPConnection("127.0.0.1", port, timeout=10)
            self.addCleanup(conn.close)

            # Frontend static
            status, html = self._request_json(conn, "GET", "/frontend/")
            self.assertEqual(status, 200)
            self.assertIn("Japan WH House Evaluator", html)

            # Spec snapshot must exist at v0.1.1 path
            status, s1 = self._request_json(conn, "GET", "/spec_bundle_v0.1.1/S1_InputSchema.json")
            self.assertEqual(status, 200)
            self.assertIsInstance(s1, dict)

            # Frontend prefecture enum includes kanagawa (driven by S1)
            pref_field = next((f for f in s1.get("fields", []) if isinstance(f, dict) and f.get("key") == "prefecture"), None)
            self.assertIsNotNone(pref_field)
            enum_values = ((pref_field or {}).get("constraints") or {}).get("enum_values") or []
            self.assertIn("kanagawa", enum_values)

            # Newer spec snapshot (v0.1.2) adds Osaka + 1DK/1LDK
            status, s1_012 = self._request_json(conn, "GET", "/spec_bundle_v0.1.2/S1_InputSchema.json")
            self.assertEqual(status, 200)
            self.assertIsInstance(s1_012, dict)

            pref_field_012 = next(
                (f for f in s1_012.get("fields", []) if isinstance(f, dict) and f.get("key") == "prefecture"), None
            )
            self.assertIsNotNone(pref_field_012)
            enum_values_012 = ((pref_field_012 or {}).get("constraints") or {}).get("enum_values") or []
            self.assertIn("osaka", enum_values_012)

            layout_field_012 = next(
                (f for f in s1_012.get("fields", []) if isinstance(f, dict) and f.get("key") == "layout_type"), None
            )
            self.assertIsNotNone(layout_field_012)
            layout_enum_012 = ((layout_field_012 or {}).get("constraints") or {}).get("enum_values") or []
            self.assertIn("1R", layout_enum_012)
            self.assertIn("1K", layout_enum_012)
            self.assertIn("1DK", layout_enum_012)
            self.assertIn("1LDK", layout_enum_012)

            hub_field_012 = next(
                (f for f in s1_012.get("fields", []) if isinstance(f, dict) and f.get("key") == "hub_station"), None
            )
            self.assertIsNotNone(hub_field_012)
            hub_enum_012 = ((hub_field_012 or {}).get("constraints") or {}).get("enum_values") or []
            self.assertIn("osaka_station", hub_enum_012)

            # Real API (mock off) - Yokohama input
            yokohama = {
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
            }
            status, out1 = self._request_json(conn, "POST", "/api/evaluate", yokohama)
            self.assertEqual(status, 200)
            self.assertEqual(out1["derived"]["benchmark_confidence"], "high")
            self.assertIsInstance(out1["report"]["summary_ko"], str)
            self.assertGreater(len(out1["report"]["summary_ko"]), 0)
            self.assertIsInstance(out1["report"]["what_if_results"], list)
            self.assertGreaterEqual(len(out1["report"]["what_if_results"]), 1)

            # What-if re-evaluation (client normally resubmits): reduce initial cost total and re-evaluate
            yokohama2 = dict(yokohama)
            yokohama2["initial_cost_total_yen"] = 205000
            status, out2 = self._request_json(conn, "POST", "/api/evaluate", yokohama2)
            self.assertEqual(status, 200)
            self.assertLess(out2["derived"]["initial_multiple"], out1["derived"]["initial_multiple"])

            # Low confidence case: no municipality -> prefecture fallback
            tokyo_low = {
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
            status, out3 = self._request_json(conn, "POST", "/api/evaluate", tokyo_low)
            self.assertEqual(status, 200)
            self.assertEqual(out3["derived"]["benchmark_confidence"], "mid")
            self.assertIsInstance(out3["report"]["summary_ko"], str)
            self.assertGreater(len(out3["report"]["summary_ko"]), 0)

            # Osaka high confidence case: municipality match + 1DK
            osaka_case = {
                "hub_station": "other",
                "hub_station_other_name": "umeda",
                "prefecture": "osaka",
                "municipality": "大阪市北区",
                "nearest_station_name": "梅田",
                "station_walk_min": 8,
                "layout_type": "1DK",
                "building_structure": "rc",
                "area_sqm": 30,
                "building_built_year": 2018,
                "orientation": "S",
                "bathroom_toilet_separate": True,
                "rent_yen": 110000,
                "mgmt_fee_yen": 10000,
                "initial_cost_total_yen": 330000,
            }
            status, out4 = self._request_json(conn, "POST", "/api/evaluate", osaka_case)
            self.assertEqual(status, 200)
            self.assertEqual(out4["derived"]["benchmark_confidence"], "high")
            self.assertIsInstance(out4["derived"].get("benchmark_monthly_fixed_cost_yen"), int)
            self.assertGreater(out4["derived"]["benchmark_monthly_fixed_cost_yen"], 0)
        finally:
            httpd.shutdown()
            t.join(timeout=5)
            httpd.server_close()
            if prev_live is None:
                os.environ.pop("SUUMO_LIVE", None)
            else:
                os.environ["SUUMO_LIVE"] = prev_live


if __name__ == "__main__":
    unittest.main()
