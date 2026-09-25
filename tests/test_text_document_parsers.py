"""真实 PDF 和 Office 文本解析回归测试。"""

import io
import unittest
import zipfile

from app.parsing_service import DocumentParsingService, ParsingPolicy
from app.state import SourceDocument
from app.tools.document_parser import DocumentParseError, DocumentParseErrorCode
from app.tools.text_document_parsers import OfficeTextParser, PDFTextParser


def _zip(parts: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, xml in parts.items():
            archive.writestr(name, xml)
    return output.getvalue()


def _pdf() -> bytes:
    """构造两页最小 PDF，第一页有文本，第二页留空供 OCR 路由测试。"""
    stream = b"BT /F1 12 Tf 72 720 Td (Booking 12345) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_offset = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode())
    return bytes(data)


class PDFTextParserTests(unittest.TestCase):
    def test_extracts_real_text_blocks_with_page_coordinates_and_ocr_routing(self) -> None:
        content = _pdf()
        source = SourceDocument("pdf-1", "notice.pdf", media_type="application/pdf")
        parsed = DocumentParsingService(
            [PDFTextParser()], ParsingPolicy(min_embedded_text_characters=5)
        ).parse(source, content)
        self.assertEqual(len(parsed.document.pages), 2)
        self.assertIn("Booking 12345", parsed.document.pages[0].text)
        self.assertGreater(parsed.document.pages[0].regions[0].right, 72)
        self.assertEqual(parsed.ocr_page_numbers, (2,))

    def test_rejects_corrupt_and_excessive_pdf(self) -> None:
        source = SourceDocument("pdf-1", "notice.pdf", media_type="application/pdf")
        with self.assertRaises(DocumentParseError) as damaged:
            PDFTextParser().parse(source, b"broken")
        self.assertEqual(damaged.exception.code, DocumentParseErrorCode.DAMAGED)

        with self.assertRaises(DocumentParseError) as oversized:
            PDFTextParser(max_pages=1).parse(source, _pdf())
        self.assertEqual(oversized.exception.code, DocumentParseErrorCode.TOO_MANY_PAGES)


class OfficeTextParserTests(unittest.TestCase):
    def test_extracts_docx_text_without_guessing_physical_pages(self) -> None:
        content = _zip({"word/document.xml": '<w:document xmlns:w="urn:w"><w:p><w:t>Vessel ABC</w:t></w:p></w:document>'})
        source = SourceDocument("doc-1", "notice.docx", media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        result = OfficeTextParser().parse(source, content)
        self.assertEqual(len(result.pages), 1)
        self.assertEqual(result.pages[0].text, "Vessel ABC")

    def test_extracts_shared_and_inline_spreadsheet_text_per_sheet(self) -> None:
        content = _zip({
            "xl/workbook.xml": "<workbook/>",
            "xl/sharedStrings.xml": '<sst xmlns="urn:x"><si><t>Booking No</t></si></sst>',
            "xl/worksheets/sheet1.xml": '<worksheet xmlns="urn:x"><c t="s"><v>0</v></c><c t="inlineStr"><is><t>123</t></is></c></worksheet>',
            "xl/worksheets/sheet2.xml": '<worksheet xmlns="urn:x"><c><v>456</v></c></worksheet>',
        })
        source = SourceDocument("sheet-1", "notice.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        result = OfficeTextParser().parse(source, content)
        self.assertEqual([page.text for page in result.pages], ["Booking No\n123", "456"])

    def test_extracts_slide_text_in_number_order(self) -> None:
        content = _zip({
            "ppt/presentation.xml": "<presentation/>",
            "ppt/slides/slide2.xml": '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>Second</a:t></p:sld>',
            "ppt/slides/slide1.xml": '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>First</a:t></p:sld>',
        })
        source = SourceDocument("slides-1", "notice.pptx", media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
        result = OfficeTextParser().parse(source, content)
        self.assertEqual([page.text for page in result.pages], ["First", "Second"])

    def test_rejects_missing_parts_and_page_limit(self) -> None:
        source = SourceDocument("slides-1", "notice.pptx", media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
        with self.assertRaises(DocumentParseError) as missing:
            OfficeTextParser().parse(source, _zip({"other.xml": "<x/>"}))
        self.assertEqual(missing.exception.code, DocumentParseErrorCode.DAMAGED)
        content = _zip({
            "ppt/presentation.xml": "<presentation/>",
            "ppt/slides/slide1.xml": "<x/>",
            "ppt/slides/slide2.xml": "<x/>",
        })
        with self.assertRaises(DocumentParseError) as oversized:
            OfficeTextParser(max_pages=1).parse(source, content)
        self.assertEqual(oversized.exception.code, DocumentParseErrorCode.TOO_MANY_PAGES)


if __name__ == "__main__":
    unittest.main()
