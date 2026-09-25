"""业务状态数据模型测试。"""

import unittest
from datetime import datetime, timezone

from app.state import (
    DocumentTask,
    FieldCandidate,
    FieldResult,
    FieldReviewAudit,
    HumanReviewStatus,
    ReviewAction,
    SourceDocument,
    SourceEvidence,
    TaskStatus,
    TaskStatusAudit,
)


class SourceEvidenceTests(unittest.TestCase):
    def test_keeps_traceable_source_information(self) -> None:
        evidence = SourceEvidence(
            document_id="notice-001",
            page_number=2,
            text_excerpt="Booking No. 276458899",
        )

        self.assertEqual(evidence.document_id, "notice-001")
        self.assertEqual(evidence.page_number, 2)
        self.assertIn("276458899", evidence.text_excerpt or "")

    def test_rejects_page_number_below_one(self) -> None:
        with self.assertRaises(ValueError):
            SourceEvidence(document_id="notice-001", page_number=0)


class FieldResultTests(unittest.TestCase):
    def test_allows_unknown_field_to_remain_empty(self) -> None:
        result = FieldResult(field_name="customer_service")

        self.assertIsNone(result.raw_value)
        self.assertIsNone(result.normalized_value)
        self.assertIsNone(result.final_value)
        self.assertEqual(result.review_status, HumanReviewStatus.UNREVIEWED)

    def test_preserves_raw_normalized_and_human_values_separately(self) -> None:
        result = FieldResult(
            field_name="carrier",
            raw_value="MAERSK LINE",
            normalized_value="马士基",
            confidence=0.96,
            review_status=HumanReviewStatus.MODIFIED,
            final_value="马士基",
            reviewed_by="operator-001",
        )

        self.assertEqual(result.raw_value, "MAERSK LINE")
        self.assertEqual(result.normalized_value, "马士基")
        self.assertEqual(result.final_value, "马士基")
        self.assertEqual(result.review_status, HumanReviewStatus.MODIFIED)

    def test_supports_multiple_candidates_with_evidence(self) -> None:
        evidence = SourceEvidence("notice-001", 1, "Booking No. 276458899")
        result = FieldResult(
            field_name="booking_no",
            candidates=[
                FieldCandidate("276458899", 0.91, (evidence,)),
                FieldCandidate("299253769", 0.54, (evidence,)),
            ],
            validation_issues=["检测到多个编号候选，需人工确认"],
        )

        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(len(result.validation_issues), 1)

    def test_rejects_confidence_outside_zero_to_one(self) -> None:
        with self.assertRaises(ValueError):
            FieldResult(field_name="carrier", confidence=1.01)

    def test_mutable_fields_are_not_shared_between_instances(self) -> None:
        first = FieldResult(field_name="carrier")
        second = FieldResult(field_name="voyage")

        first.validation_issues.append("待确认")

        self.assertEqual(second.validation_issues, [])


class DocumentTaskTests(unittest.TestCase):
    def test_creates_uploaded_task_with_utc_timestamps(self) -> None:
        task = DocumentTask(
            task_id="task-001",
            source_documents=[
                SourceDocument(
                    document_id="notice-001",
                    filename="maersk_notice.pdf",
                    media_type="application/pdf",
                )
            ],
        )

        self.assertEqual(task.status, TaskStatus.UPLOADED)
        self.assertEqual(task.source_documents[0].filename, "maersk_notice.pdf")
        self.assertIs(task.created_at.tzinfo, timezone.utc)
        self.assertIs(task.updated_at.tzinfo, timezone.utc)

    def test_task_collections_are_not_shared_between_instances(self) -> None:
        first = DocumentTask(task_id="task-001")
        second = DocumentTask(task_id="task-002")

        first.field_results["carrier"] = FieldResult(field_name="carrier")
        first.issues.append("文件需要人工处理")

        self.assertEqual(second.field_results, {})
        self.assertEqual(second.issues, [])

    def test_review_history_is_not_shared_between_tasks(self) -> None:
        first = DocumentTask(task_id="task-001")
        second = DocumentTask(task_id="task-002")
        first.review_history.append(
            FieldReviewAudit(
                field_name="carrier",
                action=ReviewAction.ACCEPTED,
                previous_status=HumanReviewStatus.UNREVIEWED,
                new_status=HumanReviewStatus.ACCEPTED,
                previous_value=None,
                new_value="马士基",
                operator="operator-1",
                reviewed_at=datetime.now(timezone.utc),
            )
        )

        self.assertEqual(second.review_history, [])

    def test_status_history_is_not_shared_between_tasks(self) -> None:
        first = DocumentTask(task_id="task-001")
        second = DocumentTask(task_id="task-002")
        first.status_history.append(
            TaskStatusAudit(
                TaskStatus.UPLOADED,
                TaskStatus.EXTRACTING,
                datetime.now(timezone.utc),
            )
        )

        self.assertEqual(second.status_history, [])


if __name__ == "__main__":
    unittest.main()
