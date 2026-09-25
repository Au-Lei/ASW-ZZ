"""人工复核领域服务测试。"""

import unittest
from datetime import datetime, timezone

from app.extraction_schema import CORE_FIELD_NAMES
from app.review_service import (
    ReviewError,
    ReviewIncompleteError,
    accept_field,
    confirm_field_empty,
    confirmed_values_for_excel,
    modify_field,
    return_field_for_reprocessing,
    summarize_review,
)
from app.state import DocumentTask, FieldResult, HumanReviewStatus, ReviewAction, TaskStatus


REVIEWED_AT = datetime(2026, 9, 26, 9, 30, tzinfo=timezone.utc)


def _review_task() -> DocumentTask:
    optional = {"salesperson", "customer_service"}
    return DocumentTask(
        task_id="task-001",
        status=TaskStatus.PENDING_REVIEW,
        field_results={
            name: FieldResult(
                name,
                raw_value=None if name in optional else f"raw-{name}",
                normalized_value="马士基" if name == "carrier" else None,
            )
            for name in CORE_FIELD_NAMES
        },
    )


class FieldReviewTests(unittest.TestCase):
    def test_accepts_normalized_value_before_raw_value(self) -> None:
        source = _review_task()

        updated = accept_field(source, "carrier", "operator-1", REVIEWED_AT)

        field = updated.field_results["carrier"]
        self.assertEqual(field.final_value, "马士基")
        self.assertEqual(field.review_status, HumanReviewStatus.ACCEPTED)
        self.assertEqual(field.reviewed_by, "operator-1")
        self.assertEqual(field.reviewed_at, REVIEWED_AT)
        self.assertEqual(
            source.field_results["carrier"].review_status,
            HumanReviewStatus.UNREVIEWED,
        )

    def test_modifies_value_and_records_before_after_audit(self) -> None:
        first = modify_field(
            _review_task(), "voyage", "638S", "operator-1", REVIEWED_AT
        )
        later = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)

        updated = modify_field(first, "voyage", "639S", "operator-2", later)

        audit = updated.review_history[-1]
        self.assertEqual(updated.field_results["voyage"].final_value, "639S")
        self.assertEqual(audit.action, ReviewAction.MODIFIED)
        self.assertEqual(audit.previous_value, "638S")
        self.assertEqual(audit.new_value, "639S")
        self.assertEqual(audit.operator, "operator-2")
        self.assertEqual(audit.reviewed_at, later)

    def test_explicitly_confirms_optional_field_empty(self) -> None:
        updated = confirm_field_empty(
            _review_task(), "customer_service", "operator-1", REVIEWED_AT
        )

        field = updated.field_results["customer_service"]
        self.assertIsNone(field.final_value)
        self.assertEqual(field.review_status, HumanReviewStatus.CONFIRMED_EMPTY)
        self.assertEqual(
            updated.review_history[-1].action,
            ReviewAction.CONFIRMED_EMPTY,
        )

    def test_rejects_accept_without_value_and_empty_manual_modification(self) -> None:
        task = _review_task()
        with self.assertRaises(ReviewError):
            accept_field(task, "salesperson", "operator-1")
        with self.assertRaises(ReviewError):
            modify_field(task, "voyage", "  ", "operator-1")

    def test_returns_field_for_reprocessing_with_reason(self) -> None:
        reviewed = modify_field(
            _review_task(), "voyage", "638S", "operator-1", REVIEWED_AT
        )
        returned = return_field_for_reprocessing(
            reviewed, "voyage", "原文页码不正确", "operator-2", REVIEWED_AT
        )

        self.assertEqual(returned.status, TaskStatus.EXTRACTING)
        self.assertEqual(
            returned.field_results["voyage"].review_status,
            HumanReviewStatus.UNREVIEWED,
        )
        self.assertIsNone(returned.field_results["voyage"].final_value)
        self.assertEqual(
            returned.review_history[-1].action,
            ReviewAction.RETURNED_FOR_REPROCESSING,
        )
        self.assertIn("原文页码不正确", returned.issues[-1])

    def test_rejects_invalid_operator_time_status_and_field(self) -> None:
        task = _review_task()
        invalid_operations = (
            lambda: accept_field(task, "carrier", ""),
            lambda: accept_field(task, "carrier", "operator", datetime(2026, 1, 1)),
            lambda: accept_field(task, "unknown", "operator"),
            lambda: accept_field(DocumentTask("new"), "carrier", "operator"),
        )
        for operation in invalid_operations:
            with self.subTest(operation=operation):
                with self.assertRaises(ReviewError):
                    operation()


class ReviewCompletionTests(unittest.TestCase):
    def test_summary_lists_missing_unreviewed_and_their_issues(self) -> None:
        task = _review_task()
        task.field_results.pop("yard")
        task.field_results["voyage"].validation_issues.append("航次待确认")

        summary = summarize_review(task)

        self.assertIn("yard", summary.missing_fields)
        self.assertIn("voyage", summary.unreviewed_fields)
        self.assertIn(("voyage", ("航次待确认",)), summary.unresolved_issues)
        self.assertFalse(summary.ready_for_excel)

    def test_all_fields_must_be_explicitly_reviewed_before_excel(self) -> None:
        task = _review_task()
        for field_name in CORE_FIELD_NAMES:
            if field_name in {"salesperson", "customer_service"}:
                task = confirm_field_empty(task, field_name, "operator", REVIEWED_AT)
            else:
                task = accept_field(task, field_name, "operator", REVIEWED_AT)

        summary = summarize_review(task)
        values = confirmed_values_for_excel(task)

        self.assertEqual(task.status, TaskStatus.CONFIRMED)
        self.assertTrue(summary.ready_for_excel)
        self.assertEqual(set(values), set(CORE_FIELD_NAMES))
        self.assertIsNone(values["salesperson"])
        self.assertEqual(values["carrier"], "马士基")
        self.assertEqual(len(task.review_history), len(CORE_FIELD_NAMES))

    def test_excel_values_are_blocked_while_any_field_is_unreviewed(self) -> None:
        task = accept_field(_review_task(), "carrier", "operator", REVIEWED_AT)

        with self.assertRaises(ReviewIncompleteError) as raised:
            confirmed_values_for_excel(task)

        self.assertIn("voyage", raised.exception.summary.unreviewed_fields)


if __name__ == "__main__":
    unittest.main()
