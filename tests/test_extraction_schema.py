"""第一阶段字段提取 Schema 测试。"""

import unittest

from app.extraction_schema import (
    CORE_FIELD_DEFINITIONS,
    CORE_FIELD_NAMES,
    validate_extraction_results,
)
from app.state import FieldCandidate, FieldResult, SourceEvidence
from app.tools.document_parser import ParsedDocument, ParsedPage
from app.tools.field_extractor import FieldExtractionError


class ExtractionSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = ParsedDocument(
            document_id="notice-001",
            pages=(ParsedPage(1, "Booking No. 276458899"),),
        )
        self.results = {
            name: FieldResult(field_name=name) for name in CORE_FIELD_NAMES
        }

    def test_defines_each_first_phase_core_field_once(self) -> None:
        self.assertEqual(len(CORE_FIELD_NAMES), 12)
        self.assertEqual(len(CORE_FIELD_NAMES), len(set(CORE_FIELD_NAMES)))
        self.assertTrue(all(item.label for item in CORE_FIELD_DEFINITIONS))
        self.assertTrue(all(item.description for item in CORE_FIELD_DEFINITIONS))

    def test_accepts_complete_schema_with_explicit_empty_values(self) -> None:
        validate_extraction_results(self.document, self.results)

    def test_rejects_missing_and_unknown_fields(self) -> None:
        self.results.pop("voyage")
        self.results["unknown"] = FieldResult(field_name="unknown")

        with self.assertRaisesRegex(
            FieldExtractionError,
            "缺少字段: voyage；未知字段: unknown",
        ):
            validate_extraction_results(self.document, self.results)

    def test_rejects_field_name_that_does_not_match_key(self) -> None:
        self.results["carrier"] = FieldResult(field_name="voyage")

        with self.assertRaisesRegex(FieldExtractionError, "字段键.*不一致"):
            validate_extraction_results(self.document, self.results)

    def test_rejects_value_without_evidence(self) -> None:
        self.results["booking_no"] = FieldResult(
            field_name="booking_no",
            raw_value="276458899",
        )

        with self.assertRaisesRegex(FieldExtractionError, "有值但缺少来源证据"):
            validate_extraction_results(self.document, self.results)

    def test_accepts_value_and_candidates_with_matching_evidence(self) -> None:
        evidence = SourceEvidence(
            "notice-001", 1, "Booking No. 276458899"
        )
        self.results["booking_no"] = FieldResult(
            field_name="booking_no",
            raw_value="276458899",
            evidence=[evidence],
            candidates=[FieldCandidate("276458899", 0.98, (evidence,))],
        )

        validate_extraction_results(self.document, self.results)

    def test_rejects_cross_document_candidate_evidence(self) -> None:
        evidence = SourceEvidence("notice-002", 1, "Booking No. 276458899")
        self.results["booking_no"].candidates.append(
            FieldCandidate("276458899", 0.75, (evidence,))
        )

        with self.assertRaisesRegex(FieldExtractionError, "指向其他文档"):
            validate_extraction_results(self.document, self.results)

    def test_rejects_evidence_with_invalid_page_or_excerpt(self) -> None:
        invalid_evidence = (
            SourceEvidence("notice-001", 2, "Booking No. 276458899"),
            SourceEvidence("notice-001", 1, "Booking No. 000000000"),
        )

        for evidence in invalid_evidence:
            with self.subTest(evidence=evidence):
                self.results["booking_no"] = FieldResult(
                    field_name="booking_no",
                    raw_value="276458899",
                    evidence=[evidence],
                )
                with self.assertRaises(FieldExtractionError):
                    validate_extraction_results(self.document, self.results)


if __name__ == "__main__":
    unittest.main()
