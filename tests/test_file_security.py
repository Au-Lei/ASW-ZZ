"""上传文件异常与恶意内容安全回归测试。"""

import io
import unittest
import warnings
import zipfile

from app.file_intake import (
    FileIntakeError,
    FileIntakePolicy,
    FileIntakeService,
    IntakeErrorCode,
)


def _archive(entries: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in entries:
                archive.writestr(name, value)
    return output.getvalue()


def _docx(extra: list[tuple[str, bytes]] | None = None) -> bytes:
    return _archive(
        [
            ("[Content_Types].xml", b"<Types />"),
            ("word/document.xml", b"<document />"),
            *(extra or []),
        ]
    )


class MaliciousFileSecurityTests(unittest.TestCase):
    def assert_rejected(
        self, filename: str, content: bytes, code: IntakeErrorCode
    ) -> None:
        with self.assertRaises(FileIntakeError) as raised:
            FileIntakeService().accept(filename, content)
        self.assertEqual(raised.exception.code, code)

    def test_rejects_pdf_active_content_and_encryption(self) -> None:
        for token, code in (
            (b"/JavaScript", IntakeErrorCode.UNSAFE_CONTENT),
            (b"/Launch", IntakeErrorCode.UNSAFE_CONTENT),
            (b"/EmbeddedFile", IntakeErrorCode.UNSAFE_CONTENT),
            (b"/Encrypt", IntakeErrorCode.ENCRYPTED),
        ):
            with self.subTest(token=token):
                self.assert_rejected(
                    "unsafe.pdf", b"%PDF-1.7\n" + token + b"\n%%EOF", code
                )

    def test_rejects_openxml_path_traversal_absolute_and_duplicate_members(self) -> None:
        malicious_entries = (
            [("../payload.exe", b"x")],
            [("C:/payload.exe", b"x")],
            [("word/document.xml", b"shadow")],
        )
        for entries in malicious_entries:
            with self.subTest(entries=entries):
                self.assert_rejected(
                    "unsafe.docx",
                    _docx(entries),
                    IntakeErrorCode.UNSAFE_CONTENT,
                )

    def test_rejects_macros_embedded_objects_and_external_links(self) -> None:
        names = (
            "word/vbaProject.bin",
            "word/embeddings/object1.bin",
            "word/externalLinks/link1.xml",
        )
        for name in names:
            with self.subTest(name=name):
                self.assert_rejected(
                    "active.docx", _docx([(name, b"payload")]), IntakeErrorCode.UNSAFE_CONTENT
                )

    def test_rejects_xml_entity_declarations(self) -> None:
        content = _archive(
            [
                ("[Content_Types].xml", b"<Types />"),
                (
                    "word/document.xml",
                    b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x/>',
                ),
            ]
        )
        self.assert_rejected("xxe.docx", content, IntakeErrorCode.UNSAFE_CONTENT)

    def test_rejects_archive_entry_count_and_expanded_size_bombs(self) -> None:
        many_entries = _docx([(f"word/items/{index}.xml", b"x") for index in range(3)])
        with self.assertRaises(FileIntakeError) as count_error:
            FileIntakeService(FileIntakePolicy(max_archive_entries=4)).accept(
                "many.docx", many_entries
            )
        self.assertEqual(count_error.exception.code, IntakeErrorCode.TOO_LARGE)

        expanded = _docx([("word/large.bin", b"0" * 10_000)])
        with self.assertRaises(FileIntakeError) as size_error:
            FileIntakeService(
                FileIntakePolicy(max_archive_uncompressed_bytes=1000)
            ).accept("bomb.docx", expanded)
        self.assertEqual(size_error.exception.code, IntakeErrorCode.TOO_LARGE)

    def test_rejects_corrupt_truncated_and_forged_files(self) -> None:
        cases = (
            ("truncated.pdf", b"%PDF-1.7\nno eof"),
            ("forged.pdf", b"\x89PNG\r\n\x1a\nIEND\xaeB`\x82"),
            ("broken.docx", _docx()[:-12]),
        )
        for filename, content in cases:
            with self.subTest(filename=filename):
                self.assert_rejected(filename, content, IntakeErrorCode.DAMAGED)

    def test_classifies_encrypted_office_container(self) -> None:
        ole_encrypted = (
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
            + b"\x00" * 32
            + b"EncryptionInfo"
            + b"EncryptedPackage"
        )
        self.assert_rejected("secret.docx", ole_encrypted, IntakeErrorCode.ENCRYPTED)

    def test_neutralizes_path_segments_and_rejects_control_characters(self) -> None:
        safe_pdf = b"%PDF-1.7\ncontent\n%%EOF"
        accepted = FileIntakeService().accept("../../windows/system32/report.pdf", safe_pdf)
        self.assertEqual(accepted.document.filename, "report.pdf")
        self.assertNotIn("report", accepted.document.stored_filename or "")
        self.assert_rejected("bad\x00name.pdf", safe_pdf, IntakeErrorCode.UNSAFE_CONTENT)

    def test_error_messages_do_not_echo_filename_or_payload(self) -> None:
        secret = "customer-secret-123"
        with self.assertRaises(FileIntakeError) as raised:
            FileIntakeService().accept(f"{secret}.exe", secret.encode())
        self.assertNotIn(secret, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
