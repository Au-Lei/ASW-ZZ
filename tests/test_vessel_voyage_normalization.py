"""船名与航次空白标准化规则测试。"""

import unittest

from app.state import FieldCandidate, FieldResult, SourceEvidence
from app.vessel_voyage_normalization import normalize_vessel_and_voyage


class NormalizeVesselAndVoyageTests(unittest.TestCase):
    def test_trims_and_collapses_vessel_name_whitespace(self) -> None:
        result = FieldResult("vessel_name", raw_value="  MAERSK\t  BERMUDA  ")

        normalized = normalize_vessel_and_voyage({"vessel_name": result})

        self.assertEqual(
            normalized["vessel_name"].normalized_value,
            "MAERSK BERMUDA",
        )

    def test_trims_voyage_without_changing_direction_suffix(self) -> None:
        cases = {
            " 638S ": "638S",
            "  0012-N  ": "0012-N",
            " ab/12e ": "ab/12e",
        }

        for raw_value, expected in cases.items():
            with self.subTest(raw_value=raw_value):
                normalized = normalize_vessel_and_voyage(
                    {"voyage": FieldResult("voyage", raw_value=raw_value)}
                )
                self.assertEqual(normalized["voyage"].normalized_value, expected)

    def test_preserves_case_punctuation_and_leading_zeroes(self) -> None:
        results = {
            "vessel_name": FieldResult(
                "vessel_name", raw_value="m/v Ever-Given II"
            ),
            "voyage": FieldResult("voyage", raw_value="007w/a"),
        }

        normalized = normalize_vessel_and_voyage(results)

        self.assertEqual(
            normalized["vessel_name"].normalized_value,
            "m/v Ever-Given II",
        )
        self.assertEqual(normalized["voyage"].normalized_value, "007w/a")

    def test_empty_or_whitespace_only_value_normalizes_to_none(self) -> None:
        results = {
            "vessel_name": FieldResult("vessel_name"),
            "voyage": FieldResult("voyage", raw_value=" \t\n "),
        }

        normalized = normalize_vessel_and_voyage(results)

        self.assertIsNone(normalized["vessel_name"].normalized_value)
        self.assertIsNone(normalized["voyage"].normalized_value)

    def test_preserves_raw_value_candidates_evidence_and_issues(self) -> None:
        evidence = SourceEvidence("notice-001", 1, "Vessel: MAERSK BERMUDA")
        source = FieldResult(
            "vessel_name",
            raw_value=" MAERSK   BERMUDA ",
            evidence=[evidence],
            candidates=[FieldCandidate("MAERSK BERMUDA")],
            validation_issues=["已有提示"],
        )

        normalized = normalize_vessel_and_voyage({"vessel_name": source})
        result = normalized["vessel_name"]

        self.assertEqual(result.raw_value, " MAERSK   BERMUDA ")
        self.assertEqual(result.evidence, [evidence])
        self.assertEqual(result.candidates, [FieldCandidate("MAERSK BERMUDA")])
        self.assertEqual(result.validation_issues, ["已有提示"])
        self.assertIsNone(source.normalized_value)

    def test_leaves_unrelated_fields_unchanged(self) -> None:
        carrier = FieldResult("carrier", raw_value="  MAERSK  ")

        normalized = normalize_vessel_and_voyage({"carrier": carrier})

        self.assertEqual(normalized["carrier"].raw_value, "  MAERSK  ")
        self.assertIsNone(normalized["carrier"].normalized_value)

    def test_rejects_mismatched_field_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "字段键.*不一致"):
            normalize_vessel_and_voyage(
                {"vessel_name": FieldResult("voyage", raw_value="638S")}
            )


if __name__ == "__main__":
    unittest.main()
