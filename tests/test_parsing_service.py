"""文档解析选择、限制及 OCR 分流测试。"""

import unittest

from app.parsing_service import DocumentParsingService, ParsingPolicy
from app.state import SourceDocument
from app.tools.document_parser import (
    DocumentParseError,
    DocumentParseErrorCode,
    ParsedDocument,
    ParsedPage,
)


class FakeParser:
    def __init__(self, media_type: str, pages: tuple[ParsedPage, ...]) -> None:
        self.media_type = media_type
        self.pages = pages

    def supports(self, media_type: str) -> bool:
        return media_type == self.media_type

    def parse(self, document: SourceDocument, content: bytes) -> ParsedDocument:
        return ParsedDocument(document.document_id, self.pages)


def _source(media_type: str | None = "application/pdf") -> SourceDocument:
    return SourceDocument("doc-1", "notice.pdf", media_type=media_type)


class DocumentParsingServiceTests(unittest.TestCase):
    def test_routes_to_matching_parser_and_identifies_only_sparse_pages(self) -> None:
        pages = (
            ParsedPage(1, "Booking number 12345 and vessel AS CLAUDIA"),
            ParsedPage(2, "  "),
            ParsedPage(3, "短文"),
        )
        result = DocumentParsingService(
            [FakeParser("application/pdf", pages)],
            ParsingPolicy(min_embedded_text_characters=5),
        ).parse(_source(), b"pdf")

        self.assertEqual(result.document.pages, pages)
        self.assertEqual(result.ocr_page_numbers, (2, 3))
        self.assertTrue(result.requires_ocr)

    def test_does_not_request_ocr_when_every_page_has_text(self) -> None:
        parser = FakeParser("application/pdf", (ParsedPage(1, "enough text"),))
        result = DocumentParsingService(
            [parser], ParsingPolicy(min_embedded_text_characters=5)
        ).parse(_source(), b"pdf")
        self.assertFalse(result.requires_ocr)

    def test_rejects_missing_or_unsupported_media_type(self) -> None:
        service = DocumentParsingService([FakeParser("application/pdf", (ParsedPage(1, "x"),))])
        for source in (_source(None), _source("image/png")):
            with self.subTest(media_type=source.media_type), self.assertRaises(DocumentParseError) as raised:
                service.parse(source, b"content")
            self.assertEqual(raised.exception.code, DocumentParseErrorCode.UNSUPPORTED)

    def test_rejects_ambiguous_parser_registration(self) -> None:
        page = (ParsedPage(1, "text"),)
        service = DocumentParsingService(
            [FakeParser("application/pdf", page), FakeParser("application/pdf", page)]
        )
        with self.assertRaises(DocumentParseError) as raised:
            service.parse(_source(), b"pdf")
        self.assertEqual(raised.exception.code, DocumentParseErrorCode.CONTRACT_VIOLATION)

    def test_enforces_page_limit_after_parser_reads_metadata(self) -> None:
        parser = FakeParser(
            "application/pdf", (ParsedPage(1, "one"), ParsedPage(2, "two"))
        )
        with self.assertRaises(DocumentParseError) as raised:
            DocumentParsingService([parser], ParsingPolicy(max_pages=1)).parse(_source(), b"pdf")
        self.assertEqual(raised.exception.code, DocumentParseErrorCode.TOO_MANY_PAGES)

    def test_rejects_parser_document_id_contract_violation(self) -> None:
        class WrongIdParser(FakeParser):
            def parse(self, document: SourceDocument, content: bytes) -> ParsedDocument:
                return ParsedDocument("wrong-id", self.pages)

        parser = WrongIdParser("application/pdf", (ParsedPage(1, "text"),))
        with self.assertRaises(DocumentParseError) as raised:
            DocumentParsingService([parser]).parse(_source(), b"pdf")
        self.assertEqual(raised.exception.code, DocumentParseErrorCode.CONTRACT_VIOLATION)

    def test_sanitizes_unexpected_parser_exception(self) -> None:
        class BrokenParser(FakeParser):
            def parse(self, document: SourceDocument, content: bytes) -> ParsedDocument:
                raise RuntimeError("secret file path and parser internals")

        parser = BrokenParser("application/pdf", (ParsedPage(1, "text"),))
        with self.assertRaises(DocumentParseError) as raised:
            DocumentParsingService([parser]).parse(_source(), b"pdf")
        self.assertEqual(raised.exception.code, DocumentParseErrorCode.PARSER_FAILURE)
        self.assertNotIn("secret", str(raised.exception))

    def test_requires_valid_policy_and_registered_parser(self) -> None:
        with self.assertRaises(ValueError):
            DocumentParsingService([])
        with self.assertRaises(ValueError):
            ParsingPolicy(max_pages=0)


if __name__ == "__main__":
    unittest.main()
