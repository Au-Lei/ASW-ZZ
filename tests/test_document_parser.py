"""文档解析器契约测试。"""

import unittest

from app.state import SourceDocument
from app.tools.document_parser import (
    DocumentParser,
    ParsedDocument,
    ParsedPage,
)


class FakeTextParser:
    """仅用于验证接口的测试替身，不负责真实文件解析。"""

    def supports(self, media_type: str) -> bool:
        return media_type == "text/plain"

    def parse(
        self,
        document: SourceDocument,
        content: bytes,
    ) -> ParsedDocument:
        return ParsedDocument(
            document_id=document.document_id,
            pages=(ParsedPage(page_number=1, text=content.decode("utf-8")),),
        )


class ParsedPageTests(unittest.TestCase):
    def test_allows_empty_text_before_ocr(self) -> None:
        page = ParsedPage(page_number=1, text="")

        self.assertEqual(page.text, "")

    def test_rejects_page_number_below_one(self) -> None:
        with self.assertRaises(ValueError):
            ParsedPage(page_number=0, text="invalid")


class ParsedDocumentTests(unittest.TestCase):
    def test_preserves_page_boundaries_and_warnings(self) -> None:
        document = ParsedDocument(
            document_id="notice-001",
            pages=(
                ParsedPage(1, "第一页"),
                ParsedPage(2, "第二页"),
            ),
            warnings=("第二页文字较少",),
        )

        self.assertEqual(len(document.pages), 2)
        self.assertEqual(document.pages[1].page_number, 2)
        self.assertEqual(document.warnings, ("第二页文字较少",))

    def test_rejects_document_without_pages(self) -> None:
        with self.assertRaises(ValueError):
            ParsedDocument(document_id="notice-001", pages=())

    def test_rejects_unsorted_or_duplicate_pages(self) -> None:
        invalid_page_sets = (
            (ParsedPage(2, "第二页"), ParsedPage(1, "第一页")),
            (ParsedPage(1, "第一页"), ParsedPage(1, "重复页")),
        )

        for pages in invalid_page_sets:
            with self.subTest(pages=pages):
                with self.assertRaises(ValueError):
                    ParsedDocument(document_id="notice-001", pages=pages)


class DocumentParserContractTests(unittest.TestCase):
    def test_compatible_parser_can_be_used_through_protocol(self) -> None:
        parser = FakeTextParser()
        source = SourceDocument(
            document_id="notice-001",
            filename="notice.txt",
            media_type="text/plain",
        )

        self.assertIsInstance(parser, DocumentParser)
        self.assertTrue(parser.supports("text/plain"))

        result = parser.parse(source, "Booking No. 276458899".encode())

        self.assertEqual(result.document_id, source.document_id)
        self.assertIn("276458899", result.pages[0].text)


if __name__ == "__main__":
    unittest.main()
