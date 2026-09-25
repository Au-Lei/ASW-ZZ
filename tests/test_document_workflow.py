"""已解析文档到待人工复核任务的集成测试。"""

import unittest
from datetime import datetime, timezone

from app.alias_mapping import MappingRegistry
from app.document_workflow import (
    DocumentWorkflow,
    DocumentWorkflowError,
    ProcessingVersions,
)
from app.extraction_schema import CORE_FIELD_NAMES
from app.field_validation import FieldValidationPolicy
from app.normalization_pipeline import RULESET_VERSION, NormalizationPipeline
from app.state import DocumentTask, FieldResult, SourceDocument, TaskStatus
from app.tools.ai_field_extractor import ManualReviewRequired
from app.tools.document_parser import ParsedDocument, ParsedPage
from app.tools.field_extractor import FieldExtractionError
from tests.fakes import FakeFieldExtractor


NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _task(status: TaskStatus = TaskStatus.UPLOADED) -> DocumentTask:
    return DocumentTask(
        task_id="task-001",
        status=status,
        source_documents=[SourceDocument("notice-001", "notice.pdf")],
    )


def _document(document_id: str = "notice-001") -> ParsedDocument:
    return ParsedDocument(document_id, (ParsedPage(1, "test document"),))


def _normalizer() -> NormalizationPipeline:
    return NormalizationPipeline(
        MappingRegistry("mapping-test-v1", ()),
        FieldValidationPolicy(low_confidence_threshold=0.8),
    )


def _versions() -> ProcessingVersions:
    return ProcessingVersions("fake-extractor-v1", "prompt-test-v1")


class RaisingExtractor:
    def __init__(self, error: FieldExtractionError) -> None:
        self.error = error

    def extract(self, document: ParsedDocument) -> dict[str, FieldResult]:
        raise self.error


class DocumentWorkflowTests(unittest.TestCase):
    def test_processes_complete_extraction_to_pending_review(self) -> None:
        responses = {
            "notice-001": {
                name: FieldResult(field_name=name) for name in CORE_FIELD_NAMES
            }
        }
        workflow = DocumentWorkflow(
            FakeFieldExtractor(CORE_FIELD_NAMES, responses),
            _normalizer(),
            _versions(),
            now_provider=lambda: NOW,
        )
        source = _task()

        result = workflow.process(source, _document())

        self.assertEqual(result.status, TaskStatus.PENDING_REVIEW)
        self.assertEqual(set(result.field_results), set(CORE_FIELD_NAMES))
        self.assertEqual(
            [item.new_status for item in result.status_history],
            [TaskStatus.EXTRACTING, TaskStatus.NORMALIZING, TaskStatus.PENDING_REVIEW],
        )
        self.assertEqual(result.extractor_version, "fake-extractor-v1")
        self.assertEqual(result.prompt_version, "prompt-test-v1")
        self.assertEqual(result.ruleset_version, RULESET_VERSION)
        self.assertEqual(result.mapping_version, "mapping-test-v1")
        self.assertEqual(source.status, TaskStatus.UPLOADED)
        self.assertEqual(source.status_history, [])

    def test_routes_exhausted_automatic_extraction_to_manual_review(self) -> None:
        workflow = DocumentWorkflow(
            RaisingExtractor(ManualReviewRequired("响应不符合字段契约", 2)),
            _normalizer(),
            _versions(),
            now_provider=lambda: NOW,
        )

        result = workflow.process(_task(), _document())

        self.assertEqual(result.status, TaskStatus.PENDING_REVIEW)
        self.assertEqual(set(result.field_results), set(CORE_FIELD_NAMES))
        self.assertTrue(all(item.raw_value is None for item in result.field_results.values()))
        self.assertIn("尝试 2 次", result.issues[-1])
        self.assertEqual(
            [item.new_status for item in result.status_history],
            [TaskStatus.EXTRACTING, TaskStatus.PENDING_REVIEW],
        )

    def test_marks_non_recoverable_extraction_contract_failure(self) -> None:
        workflow = DocumentWorkflow(
            RaisingExtractor(FieldExtractionError("bad response")),
            _normalizer(),
            _versions(),
            now_provider=lambda: NOW,
        )

        result = workflow.process(_task(), _document())

        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(result.field_results, {})
        self.assertNotIn("bad response", result.issues[-1])

    def test_rejects_incomplete_field_collection_from_extractor(self) -> None:
        workflow = DocumentWorkflow(
            FakeFieldExtractor(("carrier",)),
            _normalizer(),
            _versions(),
            now_provider=lambda: NOW,
        )

        result = workflow.process(_task(), _document())

        self.assertEqual(result.status, TaskStatus.FAILED)

    def test_rejects_document_from_another_task_or_invalid_task_status(self) -> None:
        workflow = DocumentWorkflow(
            FakeFieldExtractor(CORE_FIELD_NAMES),
            _normalizer(),
            _versions(),
            now_provider=lambda: NOW,
        )
        invalid_inputs = (
            (_task(), _document("notice-999")),
            (_task(TaskStatus.PENDING_REVIEW), _document()),
        )
        for task, document in invalid_inputs:
            with self.subTest(task=task, document=document):
                with self.assertRaises(DocumentWorkflowError):
                    workflow.process(task, document)

    def test_requires_version_values_and_timezone_aware_clock(self) -> None:
        with self.assertRaises(ValueError):
            ProcessingVersions("", "prompt-v1")
        workflow = DocumentWorkflow(
            FakeFieldExtractor(CORE_FIELD_NAMES),
            _normalizer(),
            _versions(),
            now_provider=lambda: datetime(2026, 9, 26),
        )
        with self.assertRaises(DocumentWorkflowError):
            workflow.process(_task(), _document())


if __name__ == "__main__":
    unittest.main()
