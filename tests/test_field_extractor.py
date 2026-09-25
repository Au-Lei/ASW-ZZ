"""字段提取器契约与测试替身测试。"""

import unittest

from app.state import FieldCandidate, FieldResult, SourceEvidence
from app.tools.document_parser import ParsedDocument, ParsedPage
from app.tools.field_extractor import FieldExtractor
from tests.fakes import FakeFieldExtractor


CORE_FIELDS = ("carrier", "booking_no", "contract_no", "customer_service")


class FakeFieldExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = ParsedDocument(
            document_id="notice-001",
            pages=(
                ParsedPage(1, "Carrier: MAERSK\nBooking No. 276458899"),
                ParsedPage(2, "Reference: 299253769"),
            ),
        )

    def test_satisfies_vendor_neutral_extractor_protocol(self) -> None:
        extractor = FakeFieldExtractor(CORE_FIELDS)

        self.assertIsInstance(extractor, FieldExtractor)

    def test_returns_controlled_values_with_page_evidence(self) -> None:
        booking_evidence = SourceEvidence(
            document_id="notice-001",
            page_number=1,
            text_excerpt="Booking No. 276458899",
        )
        extractor = FakeFieldExtractor(
            CORE_FIELDS,
            responses={
                "notice-001": {
                    "booking_no": FieldResult(
                        field_name="booking_no",
                        raw_value="276458899",
                        confidence=0.98,
                        evidence=[booking_evidence],
                    )
                }
            },
        )

        results = extractor.extract(self.document)

        self.assertEqual(results["booking_no"].raw_value, "276458899")
        self.assertEqual(results["booking_no"].evidence, [booking_evidence])

    def test_preserves_multiple_candidates_and_their_evidence(self) -> None:
        booking_evidence = SourceEvidence(
            "notice-001", 1, "Booking No. 276458899"
        )
        reference_evidence = SourceEvidence(
            "notice-001", 2, "Reference: 299253769"
        )
        extractor = FakeFieldExtractor(
            CORE_FIELDS,
            responses={
                "notice-001": {
                    "contract_no": FieldResult(
                        field_name="contract_no",
                        candidates=[
                            FieldCandidate("276458899", 0.65, (booking_evidence,)),
                            FieldCandidate("299253769", 0.61, (reference_evidence,)),
                        ],
                        validation_issues=["检测到多个编号候选，需人工确认"],
                    )
                }
            },
        )

        result = extractor.extract(self.document)["contract_no"]

        self.assertIsNone(result.raw_value)
        self.assertEqual([item.value for item in result.candidates], ["276458899", "299253769"])
        self.assertEqual(result.candidates[1].evidence, (reference_evidence,))

    def test_leaves_unconfigured_fields_empty_instead_of_guessing(self) -> None:
        extractor = FakeFieldExtractor(
            CORE_FIELDS,
            responses={
                "notice-001": {
                    "carrier": FieldResult(
                        field_name="carrier",
                        raw_value="MAERSK",
                    )
                }
            },
        )

        results = extractor.extract(self.document)

        self.assertIsNone(results["customer_service"].raw_value)
        self.assertIsNone(results["customer_service"].normalized_value)
        self.assertIsNone(results["customer_service"].final_value)
        self.assertIsNone(results["contract_no"].raw_value)

    def test_returns_independent_results_on_each_call(self) -> None:
        extractor = FakeFieldExtractor(CORE_FIELDS)

        first = extractor.extract(self.document)
        first["carrier"].validation_issues.append("人工确认")

        second = extractor.extract(self.document)
        self.assertEqual(second["carrier"].validation_issues, [])


if __name__ == "__main__":
    unittest.main()
