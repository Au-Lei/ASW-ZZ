"""OCR 供应商无关契约测试。"""

import unittest

from app.tools.document_parser import TextRegion
from app.tools.ocr import OCRPageResult, OCRProvider, OCRTextRegion


class FakeOCR:
    def recognize(self, page_number: int, image: bytes) -> OCRPageResult:
        return OCRPageResult(
            page_number,
            (OCRTextRegion(TextRegion("Booking 123", 1, 2, 20, 8), 0.95),),
            engine="fake-ocr",
            engine_version="1.0",
            request_id="request-1",
        )


class OCRContractTests(unittest.TestCase):
    def test_preserves_text_location_confidence_and_engine_trace(self) -> None:
        provider = FakeOCR()
        self.assertIsInstance(provider, OCRProvider)
        result = provider.recognize(2, b"image")
        self.assertEqual(result.page_number, 2)
        self.assertEqual(result.text, "Booking 123")
        self.assertEqual(result.regions[0].confidence, 0.95)
        self.assertEqual(result.engine_version, "1.0")

    def test_rejects_invalid_region_and_confidence(self) -> None:
        with self.assertRaises(ValueError):
            TextRegion("text", 5, 0, 4, 2)
        with self.assertRaises(ValueError):
            OCRTextRegion(TextRegion("text", 0, 0, 1, 1), 1.1)

    def test_requires_page_engine_and_version(self) -> None:
        with self.assertRaises(ValueError):
            OCRPageResult(0, (), "engine", "1")
        with self.assertRaises(ValueError):
            OCRPageResult(1, (), " ", "1")


if __name__ == "__main__":
    unittest.main()
