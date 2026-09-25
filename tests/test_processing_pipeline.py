"""上传到待复核端到端管线测试。"""

import hashlib
import unittest
from copy import deepcopy
from datetime import datetime, timezone

from app.alias_mapping import MappingRegistry
from app.document_workflow import DocumentWorkflow, ProcessingVersions
from app.extraction_schema import CORE_FIELD_NAMES
from app.field_validation import FieldValidationPolicy
from app.file_intake import FileIntakeService
from app.normalization_pipeline import NormalizationPipeline
from app.ocr_service import OCRPolicy, OCRProcessingService
from app.parsing_service import DocumentParsingService, ParsingPolicy
from app.processing_pipeline import (
    DocumentProcessingPipeline,
    PipelineVersions,
    ProcessingStage,
)
from app.state import SourceDocument, TaskStatus
from app.tools.document_parser import (
    DocumentParseError,
    DocumentParseErrorCode,
    ParsedDocument,
    ParsedPage,
    TextRegion,
)
from app.tools.ocr import OCRError, OCRErrorCode, OCRPageResult, OCRTextRegion
from tests.fakes import FakeFieldExtractor


NOW = datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc)
PDF = b"%PDF-1.7\nvalid test content\n%%EOF"


class FakeParser:
    def __init__(self, text: str = "enough embedded text for extraction") -> None:
        self.text = text
        self.calls = 0

    def supports(self, media_type: str) -> bool:
        return media_type == "application/pdf"

    def parse(self, document: SourceDocument, content: bytes) -> ParsedDocument:
        self.calls += 1
        return ParsedDocument(document.document_id, (ParsedPage(1, self.text),))


class FakeRenderer:
    def render(self, document_id: str, page_number: int) -> bytes:
        return b"page image"


class FakeOCR:
    def __init__(self, error: OCRError | None = None) -> None:
        self.error = error

    def recognize(self, page_number: int, image: bytes) -> OCRPageResult:
        if self.error:
            raise self.error
        return OCRPageResult(
            page_number,
            (OCRTextRegion(TextRegion("OCR extracted text", 0, 0, 10, 5), 0.9),),
            "fake-ocr",
            "2.0",
        )


def _field_workflow() -> DocumentWorkflow:
    extractor = FakeFieldExtractor(CORE_FIELD_NAMES)
    normalizer = NormalizationPipeline(
        MappingRegistry("mapping-v1", ()),
        FieldValidationPolicy(low_confidence_threshold=0.8),
    )
    return DocumentWorkflow(
        extractor,
        normalizer,
        ProcessingVersions("extractor-v1", "prompt-v1"),
        now_provider=lambda: NOW,
    )


def _pipeline(
    *,
    parser: object | None = None,
    ocr: OCRProcessingService | None = None,
    field_workflow: object | None = None,
) -> DocumentProcessingPipeline:
    return DocumentProcessingPipeline(
        FileIntakeService(),
        DocumentParsingService(
            [parser or FakeParser()],
            ParsingPolicy(min_embedded_text_characters=10),
        ),
        field_workflow or _field_workflow(),
        PipelineVersions("parser-v1"),
        ocr=ocr,
        now_provider=lambda: NOW,
    )


class DocumentProcessingPipelineTests(unittest.TestCase):
    def test_processes_upload_through_pending_review_with_versions(self) -> None:
        outcome = _pipeline().process("task-1", "notice.pdf", PDF)

        self.assertEqual(outcome.task.status, TaskStatus.PENDING_REVIEW)
        self.assertEqual(
            [audit.new_status for audit in outcome.task.status_history],
            [TaskStatus.PARSING, TaskStatus.EXTRACTING, TaskStatus.NORMALIZING, TaskStatus.PENDING_REVIEW],
        )
        self.assertEqual(outcome.task.parser_version, "parser-v1")
        self.assertEqual(outcome.task.extractor_version, "extractor-v1")
        self.assertIsNone(outcome.failed_stage)
        self.assertIsNotNone(outcome.parsed_document)

    def test_runs_ocr_only_when_parser_marks_sparse_page(self) -> None:
        ocr = OCRProcessingService(FakeOCR(), FakeRenderer())
        outcome = _pipeline(parser=FakeParser(""), ocr=ocr).process(
            "task-1", "scan.pdf", PDF
        )

        self.assertEqual(outcome.task.status, TaskStatus.PENDING_REVIEW)
        self.assertEqual(outcome.task.ocr_versions, ("fake-ocr:2.0",))
        self.assertEqual(len(outcome.ocr_traces), 1)
        self.assertIn("OCR extracted text", outcome.parsed_document.pages[0].text)  # type: ignore[union-attr]

    def test_classifies_intake_failure_without_creating_source_document(self) -> None:
        outcome = _pipeline().process("task-1", "malware.exe", b"bad")
        self.assertEqual(outcome.task.status, TaskStatus.FAILED)
        self.assertEqual(outcome.failed_stage, ProcessingStage.INTAKE)
        self.assertEqual(outcome.task.source_documents, [])
        self.assertEqual(outcome.task.issues, ["文件接入失败：unsupported"])

    def test_classifies_parser_failure_and_preserves_source_reference(self) -> None:
        class BrokenParser(FakeParser):
            def parse(self, document: SourceDocument, content: bytes) -> ParsedDocument:
                raise DocumentParseError(DocumentParseErrorCode.DAMAGED, "private detail")

        outcome = _pipeline(parser=BrokenParser()).process("task-1", "bad.pdf", PDF)
        self.assertEqual(outcome.failed_stage, ProcessingStage.PARSING)
        self.assertEqual(outcome.task.status, TaskStatus.FAILED)
        self.assertEqual(len(outcome.task.source_documents), 1)
        self.assertNotIn("private detail", outcome.task.issues[0])

    def test_missing_ocr_provider_records_recoverable_ocr_stage(self) -> None:
        outcome = _pipeline(parser=FakeParser("")).process("task-1", "scan.pdf", PDF)
        self.assertEqual(outcome.failed_stage, ProcessingStage.OCR)
        self.assertEqual(outcome.task.status, TaskStatus.FAILED)
        self.assertIsNotNone(outcome.parsed_document)

    def test_strict_ocr_failure_records_error_code(self) -> None:
        ocr = OCRProcessingService(
            FakeOCR(OCRError(OCRErrorCode.INVALID_RESPONSE, "secret")),
            FakeRenderer(),
            OCRPolicy(allow_partial_failure=False),
        )
        outcome = _pipeline(parser=FakeParser(""), ocr=ocr).process(
            "task-1", "scan.pdf", PDF
        )
        self.assertEqual(outcome.failed_stage, ProcessingStage.OCR)
        self.assertEqual(outcome.task.issues[-1], "OCR 处理失败：invalid_response")
        self.assertNotIn("secret", outcome.task.issues[-1])

    def test_marks_duplicate_but_still_processes_new_task(self) -> None:
        digest = hashlib.sha256(PDF).hexdigest()
        outcome = _pipeline().process(
            "task-1", "notice.pdf", PDF, known_hashes={"old-doc": digest}
        )
        self.assertTrue(outcome.is_duplicate)
        self.assertEqual(outcome.task.source_documents[0].duplicate_of, "old-doc")
        self.assertEqual(outcome.task.status, TaskStatus.PENDING_REVIEW)

    def test_requires_versions_and_timezone_aware_clock(self) -> None:
        with self.assertRaises(ValueError):
            PipelineVersions(" ")
        pipeline = DocumentProcessingPipeline(
            FileIntakeService(),
            DocumentParsingService([FakeParser()]),
            _field_workflow(),
            PipelineVersions("parser-v1"),
            now_provider=lambda: datetime(2026, 1, 1),
        )
        with self.assertRaises(ValueError):
            pipeline.process("task-1", "notice.pdf", PDF)

    def test_retries_parser_without_repeating_file_intake(self) -> None:
        class FlakyParser(FakeParser):
            def parse(self, document: SourceDocument, content: bytes) -> ParsedDocument:
                self.calls += 1
                if self.calls == 1:
                    raise DocumentParseError(DocumentParseErrorCode.PARSER_FAILURE, "down")
                return ParsedDocument(document.document_id, (ParsedPage(1, self.text),))

        parser = FlakyParser()
        pipeline = _pipeline(parser=parser)
        failed = pipeline.process("task-1", "notice.pdf", PDF)
        original_document_id = failed.task.source_documents[0].document_id
        recovered = pipeline.retry(failed, content=PDF)

        self.assertEqual(recovered.task.status, TaskStatus.PENDING_REVIEW)
        self.assertEqual(parser.calls, 2)
        self.assertEqual(recovered.task.source_documents[0].document_id, original_document_id)
        self.assertEqual(recovered.task.retry_history[0].stage, "parsing")
        self.assertFalse(any(issue.startswith("文档解析失败") for issue in recovered.task.issues))

    def test_retries_ocr_without_repeating_parser(self) -> None:
        parser = FakeParser("")
        provider = FakeOCR(OCRError(OCRErrorCode.INVALID_RESPONSE, "bad"))
        ocr = OCRProcessingService(
            provider, FakeRenderer(), OCRPolicy(allow_partial_failure=False)
        )
        pipeline = _pipeline(parser=parser, ocr=ocr)
        failed = pipeline.process("task-1", "scan.pdf", PDF)
        provider.error = None
        recovered = pipeline.retry(failed)

        self.assertEqual(recovered.task.status, TaskStatus.PENDING_REVIEW)
        self.assertEqual(parser.calls, 1)
        self.assertEqual(recovered.task.ocr_versions, ("fake-ocr:2.0",))
        self.assertEqual(recovered.task.retry_history[0].stage, "ocr")

    def test_retries_extraction_without_repeating_parser_or_ocr(self) -> None:
        class FlakyFieldWorkflow:
            def __init__(self) -> None:
                self.calls = 0

            def process(self, task, document):
                self.calls += 1
                result = deepcopy(task)
                if self.calls == 1:
                    result.status = TaskStatus.FAILED
                    result.issues.append("字段提取失败，需检查提取器契约或服务状态")
                else:
                    result.status = TaskStatus.PENDING_REVIEW
                return result

        parser = FakeParser()
        workflow = FlakyFieldWorkflow()
        pipeline = _pipeline(parser=parser, field_workflow=workflow)
        failed = pipeline.process("task-1", "notice.pdf", PDF)
        recovered = pipeline.retry(failed)

        self.assertEqual(recovered.task.status, TaskStatus.PENDING_REVIEW)
        self.assertEqual(parser.calls, 1)
        self.assertEqual(workflow.calls, 2)
        self.assertEqual(recovered.task.retry_history[0].stage, "extraction")
        self.assertFalse(any(issue.startswith("字段提取失败") for issue in recovered.task.issues))

    def test_rejects_retry_for_nonfailed_or_intake_failed_task(self) -> None:
        pipeline = _pipeline()
        completed = pipeline.process("task-1", "notice.pdf", PDF)
        with self.assertRaises(ValueError):
            pipeline.retry(completed)
        intake_failed = pipeline.process("task-2", "bad.exe", b"bad")
        with self.assertRaises(ValueError):
            pipeline.retry(intake_failed)


if __name__ == "__main__":
    unittest.main()
