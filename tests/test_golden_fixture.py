"""首个业务黄金样本的契约测试。"""

import json
import unittest
from pathlib import Path

from app.state import FieldResult


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "maersk_001_expected.json"
EXPECTED_CORE_FIELDS = {
    "carrier",
    "salesperson",
    "customer_service",
    "booking_no",
    "contract_no",
    "vessel_name",
    "voyage",
    "etd",
    "container_summary",
    "yard",
    "port_of_loading",
    "port_of_discharge",
}


class MaerskGoldenFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.expected_fields = cls.fixture["expected_fields"]

    def test_fixture_is_marked_as_expected_result_without_source_document(self) -> None:
        self.assertFalse(self.fixture["input_document_available"])
        self.assertIn("不代表 OCR 或 AI", self.fixture["fixture_purpose"])

    def test_fixture_contains_each_core_field_exactly_once(self) -> None:
        self.assertEqual(set(self.expected_fields), EXPECTED_CORE_FIELDS)

    def test_confirmed_sample_values_match_product_baseline(self) -> None:
        expected_subset = {
            "carrier": "马士基",
            "booking_no": "276458899",
            "contract_no": "299253769",
            "vessel_name": "MAERSK BERMUDA",
            "voyage": "638S",
            "container_summary": "1X20GP",
            "yard": "青岛港联海",
            "port_of_loading": "QD",
            "port_of_discharge": "Manila",
        }

        for field_name, expected_value in expected_subset.items():
            with self.subTest(field_name=field_name):
                self.assertEqual(self.expected_fields[field_name], expected_value)

    def test_email_derived_fields_remain_empty(self) -> None:
        self.assertIsNone(self.expected_fields["salesperson"])
        self.assertIsNone(self.expected_fields["customer_service"])

    def test_normalized_etd_records_its_year_assumption(self) -> None:
        self.assertEqual(self.expected_fields["etd"], "2026-09-30")
        self.assertEqual(self.fixture["assumptions"]["etd_year"], 2026)
        self.assertIn("人工确认", self.fixture["assumptions"]["etd_year_basis"])

    def test_expected_values_fit_the_field_result_model(self) -> None:
        results = {
            field_name: FieldResult(
                field_name=field_name,
                normalized_value=expected_value,
            )
            for field_name, expected_value in self.expected_fields.items()
        }

        self.assertEqual(set(results), EXPECTED_CORE_FIELDS)
        self.assertIsNone(results["salesperson"].normalized_value)
        self.assertEqual(results["booking_no"].normalized_value, "276458899")


if __name__ == "__main__":
    unittest.main()
