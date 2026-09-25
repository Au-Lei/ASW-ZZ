"""Booking No. 与合约号编号规则测试。"""

import unittest

from app.identifier_normalization import (
    CROSS_FIELD_IDENTIFIER_CONFLICT_ISSUE,
    MULTIPLE_IDENTIFIER_CANDIDATES_ISSUE,
    normalize_identifiers,
)
from app.state import FieldCandidate, FieldResult


class NormalizeIdentifiersTests(unittest.TestCase):
    def test_preserves_leading_zeroes_and_identifier_punctuation(self) -> None:
        results = {
            "booking_no": FieldResult("booking_no", raw_value=" 001-AB/09 "),
            "contract_no": FieldResult("contract_no", raw_value=" 00001234 "),
        }

        normalized = normalize_identifiers(results)

        self.assertEqual(normalized["booking_no"].normalized_value, "001-AB/09")
        self.assertEqual(normalized["contract_no"].normalized_value, "00001234")
        self.assertIsInstance(normalized["contract_no"].normalized_value, str)

    def test_does_not_select_from_multiple_candidates(self) -> None:
        result = FieldResult(
            "booking_no",
            candidates=[FieldCandidate("123456"), FieldCandidate("999999999999")],
        )

        normalized = normalize_identifiers({"booking_no": result})

        self.assertIsNone(normalized["booking_no"].normalized_value)
        self.assertEqual(
            normalized["booking_no"].validation_issues,
            [MULTIPLE_IDENTIFIER_CANDIDATES_ISSUE],
        )
        self.assertEqual(
            [item.value for item in normalized["booking_no"].candidates],
            ["123456", "999999999999"],
        )

    def test_marks_same_raw_value_as_cross_field_conflict(self) -> None:
        results = {
            "booking_no": FieldResult("booking_no", raw_value="ABC001"),
            "contract_no": FieldResult("contract_no", raw_value=" ABC001 "),
        }

        normalized = normalize_identifiers(results)

        for field_name in ("booking_no", "contract_no"):
            self.assertIn(
                CROSS_FIELD_IDENTIFIER_CONFLICT_ISSUE,
                normalized[field_name].validation_issues,
            )

    def test_marks_overlapping_candidates_as_cross_field_conflict(self) -> None:
        results = {
            "booking_no": FieldResult(
                "booking_no",
                candidates=[FieldCandidate("BOOK-1"), FieldCandidate("SHARED-2")],
            ),
            "contract_no": FieldResult(
                "contract_no",
                candidates=[FieldCandidate("SHARED-2"), FieldCandidate("CONT-3")],
            ),
        }

        normalized = normalize_identifiers(results)

        self.assertIn(
            CROSS_FIELD_IDENTIFIER_CONFLICT_ISSUE,
            normalized["booking_no"].validation_issues,
        )
        self.assertIn(
            CROSS_FIELD_IDENTIFIER_CONFLICT_ISSUE,
            normalized["contract_no"].validation_issues,
        )

    def test_distinct_values_do_not_create_conflict(self) -> None:
        results = {
            "booking_no": FieldResult("booking_no", raw_value="BOOK-1"),
            "contract_no": FieldResult("contract_no", raw_value="CONT-2"),
        }

        normalized = normalize_identifiers(results)

        self.assertEqual(normalized["booking_no"].validation_issues, [])
        self.assertEqual(normalized["contract_no"].validation_issues, [])

    def test_preserves_existing_issues_and_does_not_mutate_source(self) -> None:
        source = {
            "booking_no": FieldResult(
                "booking_no",
                raw_value=" 00123 ",
                validation_issues=["已有提示"],
            )
        }

        normalized = normalize_identifiers(source)

        self.assertEqual(normalized["booking_no"].validation_issues, ["已有提示"])
        self.assertEqual(source["booking_no"].raw_value, " 00123 ")
        self.assertIsNone(source["booking_no"].normalized_value)

    def test_reassessment_removes_stale_identifier_issues(self) -> None:
        source = {
            "booking_no": FieldResult(
                "booking_no",
                raw_value="BOOK-1",
                validation_issues=[CROSS_FIELD_IDENTIFIER_CONFLICT_ISSUE],
            ),
            "contract_no": FieldResult("contract_no", raw_value="CONT-2"),
        }

        normalized = normalize_identifiers(source)

        self.assertEqual(normalized["booking_no"].validation_issues, [])

    def test_rejects_mismatched_field_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "字段键.*不一致"):
            normalize_identifiers(
                {"booking_no": FieldResult("contract_no", raw_value="ABC")}
            )


if __name__ == "__main__":
    unittest.main()
