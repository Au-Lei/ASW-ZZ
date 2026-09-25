"""提取结果到标准化结果的集成测试。"""

import unittest

from app.alias_mapping import AliasMapping, EntityType, MappingRegistry
from app.extraction_schema import CORE_FIELD_NAMES
from app.field_validation import FieldValidationPolicy
from app.normalization_pipeline import RULESET_VERSION, NormalizationPipeline
from app.state import FieldResult


class NormalizationPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = MappingRegistry(
            "business-mapping-test-v1",
            (
                AliasMapping(EntityType.CARRIER, "MAERSK LINE", "马士基"),
                AliasMapping(EntityType.YARD, "港联海", "青岛港联海"),
                AliasMapping(EntityType.PORT, "QD", "青岛"),
                AliasMapping(EntityType.PORT, "Manila", "马尼拉"),
                AliasMapping(EntityType.CONTAINER_TYPE, "GP", "GP"),
                AliasMapping(EntityType.CONTAINER_TYPE, "HQ", "HC"),
            ),
        )
        self.pipeline = NormalizationPipeline(
            self.registry,
            FieldValidationPolicy(low_confidence_threshold=0.8),
        )

    def test_runs_all_rules_and_records_versions(self) -> None:
        raw_values = {
            "carrier": "MAERSK LINE",
            "salesperson": None,
            "customer_service": None,
            "booking_no": " 00276458899 ",
            "contract_no": "299253769",
            "vessel_name": " MAERSK   BERMUDA ",
            "voyage": " 638S ",
            "etd": "2026/9/30",
            "container_summary": "1X20GP + 2×40HQ",
            "yard": "港联海",
            "port_of_loading": "QD",
            "port_of_discharge": "Manila",
        }
        results = {
            name: FieldResult(
                name,
                raw_value=raw_values[name],
                confidence=0.95 if raw_values[name] is not None else None,
            )
            for name in CORE_FIELD_NAMES
        }

        normalized = self.pipeline.normalize(results)

        expected = {
            "carrier": "马士基",
            "booking_no": "00276458899",
            "contract_no": "299253769",
            "vessel_name": "MAERSK BERMUDA",
            "voyage": "638S",
            "etd": "2026-09-30",
            "container_summary": "1X20GP + 2X40HC",
            "yard": "青岛港联海",
            "port_of_loading": "青岛",
            "port_of_discharge": "马尼拉",
        }
        for field_name, expected_value in expected.items():
            with self.subTest(field_name=field_name):
                self.assertEqual(
                    normalized.field_results[field_name].normalized_value,
                    expected_value,
                )

        self.assertEqual(normalized.ruleset_version, RULESET_VERSION)
        self.assertEqual(normalized.mapping_version, "business-mapping-test-v1")
        self.assertEqual(normalized.container_items[1].type_code, "HC")
        self.assertEqual(results["carrier"].normalized_value, None)

    def test_unmapped_alias_is_not_silently_normalized(self) -> None:
        results = {
            "carrier": FieldResult(
                "carrier", raw_value="UNKNOWN LINE", confidence=0.9
            )
        }

        normalized = self.pipeline.normalize(results)
        carrier = normalized.field_results["carrier"]

        self.assertIsNone(carrier.normalized_value)
        self.assertIn("船公司未找到启用映射", carrier.validation_issues[0])

    def test_reassessment_removes_stale_container_mapping_issue(self) -> None:
        results = {
            "container_summary": FieldResult(
                "container_summary",
                raw_value="1X20GP",
                confidence=0.9,
                validation_issues=["箱型未找到启用映射，需人工确认"],
            )
        }

        normalized = self.pipeline.normalize(results)

        container = normalized.field_results["container_summary"]
        self.assertEqual(container.normalized_value, "1X20GP")
        self.assertEqual(container.validation_issues, [])


if __name__ == "__main__":
    unittest.main()
