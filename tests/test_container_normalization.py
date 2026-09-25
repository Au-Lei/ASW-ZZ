"""箱量及类型解析规则测试。"""

import unittest

from app.container_normalization import (
    CONTAINER_UNSUPPORTED_FORMAT_ISSUE,
    ContainerItem,
    normalize_container_summary,
)
from app.state import FieldResult, SourceEvidence


class NormalizeContainerSummaryTests(unittest.TestCase):
    def test_parses_supported_single_item_formats(self) -> None:
        cases = {
            "1X20GP": ("1X20GP", ContainerItem(1, 20, "GP")),
            "2×40HQ": ("2X40HQ", ContainerItem(2, 40, "HQ")),
            "3 x 40' hc": ("3X40HC", ContainerItem(3, 40, "HC")),
            "1*45RF": ("1X45RF", ContainerItem(1, 45, "RF")),
        }

        for raw_value, (expected_text, expected_item) in cases.items():
            with self.subTest(raw_value=raw_value):
                result = normalize_container_summary(
                    FieldResult("container_summary", raw_value=raw_value)
                )
                self.assertEqual(result.field_result.normalized_value, expected_text)
                self.assertEqual(result.items, (expected_item,))

    def test_parses_multiple_items_without_combining_them(self) -> None:
        result = normalize_container_summary(
            FieldResult(
                "container_summary",
                raw_value="1X20GP + 2×40HQ，1 x 45 RF",
            )
        )

        self.assertEqual(
            result.field_result.normalized_value,
            "1X20GP + 2X40HQ + 1X45RF",
        )
        self.assertEqual(
            result.items,
            (
                ContainerItem(1, 20, "GP"),
                ContainerItem(2, 40, "HQ"),
                ContainerItem(1, 45, "RF"),
            ),
        )

    def test_rejects_incomplete_or_free_text_without_guessing(self) -> None:
        invalid_values = (
            "1X20",
            "20GP",
            "X40HQ",
            "0X20GP",
            "约两个40尺高箱",
            "1X30GP",
        )

        for raw_value in invalid_values:
            with self.subTest(raw_value=raw_value):
                result = normalize_container_summary(
                    FieldResult("container_summary", raw_value=raw_value)
                )
                self.assertIsNone(result.field_result.normalized_value)
                self.assertEqual(result.items, ())
                self.assertEqual(
                    result.field_result.validation_issues,
                    [CONTAINER_UNSUPPORTED_FORMAT_ISSUE],
                )

    def test_preserves_original_display_value_evidence_and_existing_issues(self) -> None:
        evidence = SourceEvidence("notice-001", 1, "Container: 2×40HQ")
        source = FieldResult(
            "container_summary",
            raw_value=" 2×40HQ ",
            evidence=[evidence],
            validation_issues=["已有提示"],
        )

        result = normalize_container_summary(source)

        self.assertEqual(result.field_result.raw_value, " 2×40HQ ")
        self.assertEqual(result.field_result.evidence, [evidence])
        self.assertEqual(result.field_result.validation_issues, ["已有提示"])
        self.assertIsNone(source.normalized_value)

    def test_empty_value_remains_empty_for_general_missing_rule(self) -> None:
        result = normalize_container_summary(FieldResult("container_summary"))

        self.assertIsNone(result.field_result.normalized_value)
        self.assertEqual(result.field_result.validation_issues, [])
        self.assertEqual(result.items, ())

    def test_reassessment_clears_stale_container_result_and_issue(self) -> None:
        source = FieldResult(
            "container_summary",
            raw_value="2X40HQ",
            normalized_value="错误旧值",
            validation_issues=[CONTAINER_UNSUPPORTED_FORMAT_ISSUE],
        )

        result = normalize_container_summary(source)

        self.assertEqual(result.field_result.normalized_value, "2X40HQ")
        self.assertEqual(result.field_result.validation_issues, [])

    def test_rejects_non_container_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "只能处理 container_summary"):
            normalize_container_summary(FieldResult("voyage", raw_value="1X20GP"))


if __name__ == "__main__":
    unittest.main()
