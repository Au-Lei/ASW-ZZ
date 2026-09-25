"""文件接入安全校验测试。"""

import hashlib
import io
import unittest
import zipfile

from app.file_intake import (
    FileIntakeError,
    FileIntakePolicy,
    FileIntakeService,
    IntakeErrorCode,
)


def _pdf(body: bytes = b"1 0 obj << /Type /Catalog >> endobj") -> bytes:
    return b"%PDF-1.7\n" + body + b"\n%%EOF"


def _openxml(part: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr(part, "<root />")
    return output.getvalue()


class FileIntakeServiceTests(unittest.TestCase):
    def test_accepts_pdf_and_generates_controlled_metadata(self) -> None:
        content = _pdf()
        result = FileIntakeService().accept("../客户上传/订舱 单.pdf", content)

        document = result.document
        self.assertEqual(document.filename, "订舱 单.pdf")
        self.assertEqual(document.media_type, "application/pdf")
        self.assertEqual(document.content_hash, hashlib.sha256(content).hexdigest())
        self.assertEqual(document.byte_size, len(content))
        self.assertRegex(document.stored_filename or "", r"^[0-9a-f]{32}\.pdf$")
        self.assertFalse(result.is_duplicate)

    def test_marks_duplicate_without_reusing_storage_name(self) -> None:
        content = _pdf()
        digest = hashlib.sha256(content).hexdigest()
        result = FileIntakeService().accept(
            "notice.pdf", content, known_hashes={"previous-doc": digest}
        )

        self.assertTrue(result.is_duplicate)
        self.assertEqual(result.document.duplicate_of, "previous-doc")
        self.assertNotEqual(result.document.document_id, "previous-doc")

    def test_rejects_empty_oversized_and_unsupported_files(self) -> None:
        cases = (
            ("empty.pdf", b"", IntakeErrorCode.EMPTY),
            ("large.pdf", _pdf(b"123456789"), IntakeErrorCode.TOO_LARGE),
            ("legacy.doc", b"content", IntakeErrorCode.UNSUPPORTED),
        )
        service = FileIntakeService(FileIntakePolicy(max_bytes=16))
        for filename, content, expected in cases:
            with self.subTest(filename=filename), self.assertRaises(FileIntakeError) as raised:
                service.accept(filename, content)
            self.assertEqual(raised.exception.code, expected)

    def test_rejects_declared_type_or_content_mismatch(self) -> None:
        service = FileIntakeService()
        with self.assertRaises(FileIntakeError) as declared:
            service.accept("notice.pdf", _pdf(), declared_media_type="image/png")
        self.assertEqual(declared.exception.code, IntakeErrorCode.TYPE_MISMATCH)

        with self.assertRaises(FileIntakeError) as forged:
            service.accept("notice.pdf", b"not a pdf")
        self.assertEqual(forged.exception.code, IntakeErrorCode.DAMAGED)

    def test_classifies_encrypted_pdf(self) -> None:
        with self.assertRaises(FileIntakeError) as raised:
            FileIntakeService().accept("secret.pdf", _pdf(b"trailer << /Encrypt 2 0 R >>"))
        self.assertEqual(raised.exception.code, IntakeErrorCode.ENCRYPTED)

    def test_validates_png_and_jpeg_boundaries(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"data" + b"IEND\xaeB`\x82"
        jpeg = b"\xff\xd8\xff" + b"data" + b"\xff\xd9"
        self.assertEqual(FileIntakeService().accept("a.PNG", png).document.media_type, "image/png")
        self.assertEqual(FileIntakeService().accept("a.jpeg", jpeg).document.media_type, "image/jpeg")

    def test_validates_each_openxml_container_kind(self) -> None:
        cases = (
            ("a.docx", "word/document.xml"),
            ("a.xlsx", "xl/workbook.xml"),
            ("a.pptx", "ppt/presentation.xml"),
        )
        for filename, part in cases:
            with self.subTest(filename=filename):
                result = FileIntakeService().accept(filename, _openxml(part))
                self.assertEqual(result.document.filename, filename)

        with self.assertRaises(FileIntakeError) as mismatch:
            FileIntakeService().accept("wrong.docx", _openxml("xl/workbook.xml"))
        self.assertEqual(mismatch.exception.code, IntakeErrorCode.TYPE_MISMATCH)

    def test_limits_openxml_expanded_size(self) -> None:
        policy = FileIntakePolicy(max_archive_uncompressed_bytes=10)
        with self.assertRaises(FileIntakeError) as raised:
            FileIntakeService(policy).accept("large.docx", _openxml("word/document.xml"))
        self.assertEqual(raised.exception.code, IntakeErrorCode.TOO_LARGE)

    def test_source_metadata_rejects_negative_size(self) -> None:
        from app.state import SourceDocument

        with self.assertRaises(ValueError):
            SourceDocument("doc", "a.pdf", byte_size=-1)


if __name__ == "__main__":
    unittest.main()
