"""按需 OCR、重试、合并和部分失败测试。"""

import unittest

from app.ocr_service import (
    OCREnrichmentResult,
    OCRPolicy,
    OCRProcessingError,
    OCRProcessingService,
    PageImageRenderer,
)
from app.parsing_service import ParsingResult
from app.tools.document_parser import ParsedDocument, ParsedPage, TextRegion
from app.tools.ocr import OCRError, OCRErrorCode, OCRPageResult, OCRTextRegion


class FakeRenderer:
    def render(self, document_id: str, page_number: int) -> bytes:
        return f"{document_id}:{page_number}".encode()


class SequenceOCR:
    def __init__(self, outcomes: list[OCRPageResult | Exception]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def recognize(self, page_number: int, image: bytes) -> OCRPageResult:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _result(page_number: int = 2) -> OCRPageResult:
    return OCRPageResult(
        page_number,
        (OCRTextRegion(TextRegion("OCR Booking 123", 1, 2, 30, 10), 0.92),),
        "fake",
        "1.0",
        "req-1",
    )


def _parsing(targets: tuple[int, ...] = (2,)) -> ParsingResult:
    return ParsingResult(
        ParsedDocument(
            "doc-1",
            (ParsedPage(1, "embedded page text"), ParsedPage(2, "short")),
            ("existing warning",),
        ),
        targets,
    )


class OCRProcessingServiceTests(unittest.TestCase):
    def test_merges_only_target_page_and_keeps_existing_text_and_evidence(self) -> None:
        service = OCRProcessingService(SequenceOCR([_result()]), FakeRenderer())
        enriched = service.enrich(_parsing())

        self.assertIsInstance(enriched, OCREnrichmentResult)
        self.assertEqual(enriched.document.pages[0].text, "embedded page text")
        self.assertEqual(enriched.document.pages[1].text, "short\nOCR Booking 123")
        self.assertEqual(enriched.document.pages[1].regions[0].left, 1)
        self.assertEqual(enriched.document.warnings, ("existing warning",))
        self.assertEqual(enriched.traces[0].engine_version, "1.0")
        self.assertEqual(enriched.failed_page_numbers, ())

    def test_retries_transient_error_then_succeeds(self) -> None:
        provider = SequenceOCR(
            [OCRError(OCRErrorCode.TIMEOUT, "timeout detail"), _result()]
        )
        enriched = OCRProcessingService(provider, FakeRenderer()).enrich(_parsing())
        self.assertEqual(provider.calls, 2)
        self.assertEqual(enriched.traces[0].attempts, 2)
        self.assertTrue(enriched.traces[0].succeeded)

    def test_does_not_retry_invalid_response(self) -> None:
        provider = SequenceOCR(
            [OCRError(OCRErrorCode.INVALID_RESPONSE, "raw response")]
        )
        enriched = OCRProcessingService(provider, FakeRenderer()).enrich(_parsing())
        self.assertEqual(provider.calls, 1)
        self.assertEqual(enriched.failed_page_numbers, (2,))
        self.assertIn("invalid_response", enriched.document.warnings[-1])
        self.assertNotIn("raw response", enriched.document.warnings[-1])

    def test_partial_failure_keeps_original_page_and_other_results(self) -> None:
        parsing = _parsing((1, 2))
        provider = SequenceOCR(
            [
                _result(1),
                OCRError(OCRErrorCode.SERVICE_UNAVAILABLE, "down"),
                OCRError(OCRErrorCode.SERVICE_UNAVAILABLE, "still down"),
            ]
        )
        enriched = OCRProcessingService(provider, FakeRenderer()).enrich(parsing)
        self.assertIn("OCR Booking 123", enriched.document.pages[0].text)
        self.assertEqual(enriched.document.pages[1].text, "short")
        self.assertEqual(enriched.failed_page_numbers, (2,))

    def test_strict_mode_raises_safe_classified_error(self) -> None:
        provider = SequenceOCR([OCRError(OCRErrorCode.INVALID_RESPONSE, "secret")])
        service = OCRProcessingService(
            provider, FakeRenderer(), OCRPolicy(allow_partial_failure=False)
        )
        with self.assertRaises(OCRProcessingError) as raised:
            service.enrich(_parsing())
        self.assertEqual(raised.exception.code, OCRErrorCode.INVALID_RESPONSE)
        self.assertNotIn("secret", str(raised.exception))

    def test_rejects_target_page_outside_document(self) -> None:
        with self.assertRaises(ValueError):
            OCRProcessingService(SequenceOCR([]), FakeRenderer()).enrich(_parsing((3,)))

    def test_renderer_failure_is_classified_without_calling_provider(self) -> None:
        class BrokenRenderer:
            def render(self, document_id: str, page_number: int) -> bytes:
                raise RuntimeError("private path")

        provider = SequenceOCR([])
        enriched = OCRProcessingService(provider, BrokenRenderer()).enrich(_parsing())
        self.assertEqual(provider.calls, 0)
        self.assertEqual(enriched.traces[0].error_code, OCRErrorCode.UNSUPPORTED_IMAGE)

    def test_contract_and_policy_runtime_checks(self) -> None:
        self.assertIsInstance(FakeRenderer(), PageImageRenderer)
        with self.assertRaises(ValueError):
            OCRPolicy(max_attempts=0)


if __name__ == "__main__":
    unittest.main()
