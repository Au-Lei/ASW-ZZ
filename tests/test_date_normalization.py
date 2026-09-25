"""船期日期标准化规则测试。"""

import unittest

from app.date_normalization import (
    DATE_INVALID_ISSUE,
    DATE_UNSUPPORTED_FORMAT_ISSUE,
    DATE_YEAR_MISSING_ISSUE,
    normalize_etd,
)
from app.state import FieldResult, SourceEvidence


class NormalizeEtdTests(unittest.TestCase):
    def test_normalizes_unambiguous_dates_with_explicit_year(self) -> None:
        cases = {
            "2026-09-30": "2026-09-30",
            "2026/9/30": "2026-09-30",
            "2026年9月30日": "2026-09-30",
            "30 SEP 2026": "2026-09-30",
            "September 30, 2026": "2026-09-30",
        }

        for raw_value, expected in cases.items():
            with self.subTest(raw_value=raw_value):
                result = normalize_etd(FieldResult("etd", raw_value=raw_value))
                self.assertEqual(result.normalized_value, expected)
                self.assertEqual(result.validation_issues, [])

    def test_does_not_guess_year_for_partial_date(self) -> None:
        for raw_value in ("9.30", "SEP 30", "30 September"):
            with self.subTest(raw_value=raw_value):
                result = normalize_etd(FieldResult("etd", raw_value=raw_value))
                self.assertIsNone(result.normalized_value)
                self.assertEqual(result.validation_issues, [DATE_YEAR_MISSING_ISSUE])

    def test_marks_invalid_explicit_date(self) -> None:
        result = normalize_etd(FieldResult("etd", raw_value="2026-02-30"))

        self.assertIsNone(result.normalized_value)
        self.assertEqual(result.validation_issues, [DATE_INVALID_ISSUE])

    def test_does_not_convert_ambiguous_or_unsupported_format(self) -> None:
        for raw_value in ("30/09/2026", "ETD 2026-09-30"):
            with self.subTest(raw_value=raw_value):
                result = normalize_etd(FieldResult("etd", raw_value=raw_value))
                self.assertIsNone(result.normalized_value)
                self.assertEqual(
                    result.validation_issues,
                    [DATE_UNSUPPORTED_FORMAT_ISSUE],
                )

    def test_preserves_raw_value_evidence_and_existing_unrelated_issues(self) -> None:
        evidence = SourceEvidence("notice-001", 1, "ETD: 2026-09-30")
        source = FieldResult(
            "etd",
            raw_value="2026-09-30",
            evidence=[evidence],
            validation_issues=["已有提示"],
        )

        result = normalize_etd(source)

        self.assertEqual(result.raw_value, "2026-09-30")
        self.assertEqual(result.evidence, [evidence])
        self.assertEqual(result.validation_issues, ["已有提示"])
        self.assertIsNone(source.normalized_value)

    def test_removes_stale_date_result_before_reassessing(self) -> None:
        source = FieldResult(
            "etd",
            raw_value="9.30",
            normalized_value="2026-09-30",
            validation_issues=[DATE_INVALID_ISSUE],
        )

        result = normalize_etd(source)

        self.assertIsNone(result.normalized_value)
        self.assertEqual(result.validation_issues, [DATE_YEAR_MISSING_ISSUE])

    def test_empty_value_remains_empty_without_duplicate_missing_issue(self) -> None:
        source = FieldResult("etd", validation_issues=["字段缺失，需人工确认"])

        result = normalize_etd(source)

        self.assertIsNone(result.normalized_value)
        self.assertEqual(result.validation_issues, ["字段缺失，需人工确认"])

    def test_rejects_non_etd_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "只能处理 etd"):
            normalize_etd(FieldResult("voyage", raw_value="2026-09-30"))


if __name__ == "__main__":
    unittest.main()
