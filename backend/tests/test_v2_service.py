import copy
import json
import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.v2.costs import calculate_costs
from backend.v2.demo import demo_subject
from backend.v2.models import ValidationError
from backend.v2.service import Runtime, approved_segments
from backend.v2.approval import policy_digest, REQUIRED_MARKET_GATES

NOW = datetime(2026, 9, 13, 0, 0, tzinfo=timezone.utc)


class CostsTest(unittest.TestCase):
    def setUp(self):
        self.payload = dict(rent_yen=80000, mgmt_fee_yen=5000, monthly_extra_yen=1000,
                            stay_months=12, initial_payment_yen=300000, prepaid_rent_yen=86000,
                            refundable_deposit_yen=80000, exit_fee_yen=30000,
                            renewal_fee_yen=0, discount_yen=10000)

    def test_cash_flow_and_refund_scenario(self):
        result = calculate_costs(self.payload)
        self.assertEqual(result['monthly_base_yen'], 85000)
        self.assertEqual(result['monthly_all_in_yen'], 86000)
        self.assertEqual(result['nonrefundable_initial_yen'], 134000)
        self.assertEqual(result['stay_cost_yen'], 1186000)
        self.assertEqual(result['deposit_at_risk_yen'], 80000)
        self.assertEqual(result['upfront_cash_yen'], 300000)

    def test_prepayment_does_not_change_total_cost(self):
        before = calculate_costs(self.payload)
        self.payload['prepaid_rent_yen'] += 86000
        self.payload['initial_payment_yen'] += 86000
        after = calculate_costs(self.payload)
        self.assertEqual(before['stay_cost_yen'], after['stay_cost_yen'])
        self.assertEqual(after['upfront_cash_yen'] - before['upfront_cash_yen'], 86000)

    def test_missing_unknown_and_invalid_costs(self):
        cases = [{'rent_yen': True}, {'rent_yen': 0}, {'rent_yen': float('nan')},
                 {'stay_months': 61}, {'mgmt_fee_yen': -1}, {'discount_yen': 99999999},
                 {'initial_payment_yen': 1}, {'prepaid_rent_yen': 99999999}, {'invented': 0}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                calculate_costs({**self.payload, **changes})
        del self.payload['mgmt_fee_yen']
        with self.assertRaises(ValidationError):
            calculate_costs(self.payload)


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = Runtime(self.root / 'data.sqlite3', demo_enabled=True)

    def test_market_never_falls_back_to_demo(self):
        payload = {'subject': demo_subject('tokyo'), 'mode': 'demo'}
        demo = self.runtime.evaluate(payload, now=NOW)
        self.assertTrue(demo['is_demo'])
        self.assertIsNone(demo['judgment'])
        self.assertGreater(demo['sample']['unit_count'], 0)
        market = self.runtime.evaluate({**payload, 'mode': 'market'}, now=NOW)
        self.assertFalse(market['is_demo'])
        self.assertIsNone(market['judgment'])
        self.assertIsNone(market['benchmark_yen'])
        self.assertEqual(market['sample']['unit_count'], 0)
        self.assertFalse(self.runtime.capabilities(now=NOW)['market_data_available'])
        self.assertNotEqual(demo['versions']['snapshot'], market['versions']['snapshot'])

    def test_demo_all_cities_and_idempotent_receipt(self):
        for city in ('tokyo', 'osaka', 'fukuoka'):
            payload = {'subject': demo_subject(city), 'mode': 'demo'}
            first = self.runtime.evaluate(payload, now=NOW)
            second = self.runtime.evaluate(payload, now=NOW)
            self.assertEqual(first['assessment_id'], second['assessment_id'])
            self.assertIsNone(first['judgment'])
            self.assertGreater(first['sample']['building_count'], 0)
            self.assertEqual(first['receipt']['mode'], 'demo')

    def test_demo_disabled_and_invalid_request(self):
        disabled = Runtime(self.root / 'disabled.sqlite3')
        with self.assertRaises(PermissionError):
            disabled.evaluate({'subject': demo_subject('tokyo'), 'mode': 'demo'}, now=NOW)
        for payload in ({'subject': {}, 'mode': []}, {'subject': {}, 'validated_segments': ['tokyo/1K']}, [], {}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                disabled.evaluate(payload, now=NOW)

    def test_approval_requires_current_source_report_and_expiry(self):
        registry_path = self.root / 'registry.json'
        # A unit fixture for approval parsing, not a real market validation report.
        report = {'schema_version': 'market-validation-1.0', 'report_kind': 'market',
                  'policy_version': 'direct-v2.0', 'policy_sha256': policy_digest(),
                  'source_ids': ['licensed-a'], 'generated_at': (NOW - timedelta(days=2)).isoformat(),
                  'eligible_segments': ['tokyo/1K'], 'eligibility': {'passed': True},
                  'segments': {'tokyo/1K': {'eligible_for_approval': True, 'gates': {key: True for key in REQUIRED_MARKET_GATES}}}}
        raw = json.dumps(report).encode('utf-8')
        (self.root / 'report.json').write_bytes(raw)
        entry = dict(policy_version='direct-v2.0', approved=True, source_ids=['licensed-a'],
                     validated_at=(NOW - timedelta(days=1)).isoformat(), expires_at=(NOW + timedelta(days=30)).isoformat(),
                     report_path='report.json', report_sha256=hashlib.sha256(raw).hexdigest(), approved_by='test-operator', segments=['tokyo/1K'])
        def write(value):
            registry_path.write_text(json.dumps({'schema_version': '2.0', 'reports': [value]}), encoding='utf-8')
        write(entry)
        self.assertEqual(approved_segments(registry_path, {'licensed-a'}, NOW), {'tokyo/1K'})
        self.assertEqual(approved_segments(registry_path, {'different-source'}, NOW), set())
        self.assertEqual(approved_segments(registry_path, set(), NOW), set())
        for changes in ({'expires_at': NOW.isoformat()}, {'approved': False}, {'approved_by': ''},
                        {'report_path': '../outside.md'}, {'report_path': 'missing.md'}, {'policy_version': 'other'},
                        {'validated_at': (NOW + timedelta(days=1)).isoformat()}):
            write({**entry, **changes})
            self.assertEqual(approved_segments(registry_path, {'licensed-a'}, NOW), set())
        registry_path.write_text('[]', encoding='utf-8')
        self.assertEqual(approved_segments(registry_path, {'licensed-a'}, NOW), set())
