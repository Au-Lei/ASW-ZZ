"""确定性字段问题标记规则测试。"""

import unittest

from app.field_validation import (
    LOW_CONFIDENCE_ISSUE,
    MISSING_CONFIDENCE_ISSUE,
    MISSING_VALUE_ISSUE,
    MULTIPLE_CANDIDATES_ISSUE,
    FieldValidationPolicy,
    assess_field_results,
)
from app.state import FieldCandidate, FieldResult


class FieldValidationPolicyTests(unittest.TestCase):
    def test_requires_valid_explicit_confidence_threshold(self) -> None:
        for threshold in (-0.01, 1.01, True, "0.8"):
            with self.subTest(threshold=threshold):
                with self.assertRaises(ValueError):
                    FieldValidationPolicy(threshold)  # type: ignore[arg-type]

    def test_rejects_unknown_optional_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知字段"):
            FieldValidationPolicy(0.8, frozenset({"not_a_core_field"}))


class AssessFieldResultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = FieldValidationPolicy(low_confidence_threshold=0.8)

    def test_marks_missing_required_field_but_allows_optional_empty_fields(self) -> None:
        results = {
            "carrier": FieldResult(field_name="carrier"),
            "salesperson": FieldResult(field_name="salesperson"),
            "customer_service": FieldResult(field_name="customer_service"),
        }

        assessed = assess_field_results(results, self.policy)

        self.assertEqual(assessed["carrier"].validation_issues, [MISSING_VALUE_ISSUE])
        self.assertEqual(assessed["salesperson"].validation_issues, [])
        self.assertEqual(assessed["customer_service"].validation_issues, [])

    def test_marks_low_or_missing_confidence_only_for_extracted_value(self) -> None:
        results = {
            "carrier": FieldResult("carrier", raw_value="MAERSK", confidence=0.79),
            "voyage": FieldResult("voyage", raw_value="638S"),
            "yard": FieldResult("yard"),
        }

        assessed = assess_field_results(results, self.policy)

        self.assertIn(LOW_CONFIDENCE_ISSUE, assessed["carrier"].validation_issues)
        self.assertIn(MISSING_CONFIDENCE_ISSUE, assessed["voyage"].validation_issues)
        self.assertNotIn(MISSING_CONFIDENCE_ISSUE, assessed["yard"].validation_issues)

    def test_threshold_is_inclusive_for_acceptable_confidence(self) -> None:
        result = FieldResult("carrier", raw_value="MAERSK", confidence=0.8)

        assessed = assess_field_results({"carrier": result}, self.policy)

        self.assertNotIn(LOW_CONFIDENCE_ISSUE, assessed["carrier"].validation_issues)

    def test_marks_multiple_candidates_without_also_marking_value_missing(self) -> None:
        result = FieldResult(
            "contract_no",
            candidates=[FieldCandidate("A"), FieldCandidate("B")],
        )

        assessed = assess_field_results({"contract_no": result}, self.policy)

        self.assertEqual(
            assessed["contract_no"].validation_issues,
            [MULTIPLE_CANDIDATES_ISSUE],
        )

    def test_preserves_existing_issues_and_is_idempotent(self) -> None:
        result = FieldResult("carrier", validation_issues=["已有提示"])

        first = assess_field_results({"carrier": result}, self.policy)
        second = assess_field_results(first, self.policy)

        self.assertEqual(second["carrier"].validation_issues, ["已有提示", MISSING_VALUE_ISSUE])

    def test_does_not_mutate_source_results(self) -> None:
        source = {"carrier": FieldResult("carrier")}

        assessed = assess_field_results(source, self.policy)

        self.assertEqual(source["carrier"].validation_issues, [])
        self.assertIsNot(source["carrier"], assessed["carrier"])


if __name__ == "__main__":
    unittest.main()
