"""与界面无关的人工复核领域服务。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone

from app.extraction_schema import CORE_FIELD_NAMES
from app.state import (
    DocumentTask,
    FieldResult,
    FieldReviewAudit,
    HumanReviewStatus,
    ReviewAction,
    TaskStatus,
)


class ReviewError(Exception):
    """人工复核操作不符合领域约束。"""


class ReviewIncompleteError(ReviewError):
    """任务尚未完成全部字段复核。"""

    def __init__(self, summary: ReviewSummary) -> None:
        super().__init__("任务仍有未解决字段，不能生成 Excel")
        self.summary = summary


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    missing_fields: tuple[str, ...]
    unreviewed_fields: tuple[str, ...]
    unresolved_issues: tuple[tuple[str, tuple[str, ...]], ...]
    ready_for_excel: bool


def accept_field(
    task: DocumentTask,
    field_name: str,
    operator: str,
    reviewed_at: datetime | None = None,
) -> DocumentTask:
    """接受标准化建议；没有建议时接受原始提取值。"""

    updated, result, timestamp = _prepare_review(task, field_name, operator, reviewed_at)
    accepted_value = result.normalized_value or result.raw_value
    if accepted_value is None:
        raise ReviewError("字段没有可接受的建议值；请选择候选、修改或明确留空")
    return _record_field_review(
        updated, result, ReviewAction.ACCEPTED, HumanReviewStatus.ACCEPTED,
        accepted_value, operator, timestamp,
    )


def modify_field(
    task: DocumentTask,
    field_name: str,
    value: str,
    operator: str,
    reviewed_at: datetime | None = None,
) -> DocumentTask:
    """记录人工明确输入的非空最终值。"""

    if not isinstance(value, str) or not value.strip():
        raise ReviewError("人工修改值不能为空；如需留空请使用明确留空操作")
    updated, result, timestamp = _prepare_review(task, field_name, operator, reviewed_at)
    return _record_field_review(
        updated, result, ReviewAction.MODIFIED, HumanReviewStatus.MODIFIED,
        value.strip(), operator, timestamp,
    )


def confirm_field_empty(
    task: DocumentTask,
    field_name: str,
    operator: str,
    reviewed_at: datetime | None = None,
) -> DocumentTask:
    """由人工明确确认字段应保持为空。"""

    updated, result, timestamp = _prepare_review(task, field_name, operator, reviewed_at)
    return _record_field_review(
        updated, result, ReviewAction.CONFIRMED_EMPTY,
        HumanReviewStatus.CONFIRMED_EMPTY, None, operator, timestamp,
    )


def return_field_for_reprocessing(
    task: DocumentTask,
    field_name: str,
    reason: str,
    operator: str,
    reviewed_at: datetime | None = None,
) -> DocumentTask:
    """退回字段重新提取，并保留退回原因和历史记录。"""

    if not reason.strip():
        raise ReviewError("退回重处理必须填写原因")
    updated, result, timestamp = _prepare_review(task, field_name, operator, reviewed_at)
    previous_status = result.review_status
    previous_value = result.final_value
    result.review_status = HumanReviewStatus.UNREVIEWED
    result.final_value = None
    result.reviewed_by = operator
    result.reviewed_at = timestamp
    updated.review_history.append(
        FieldReviewAudit(
            field_name=field_name,
            action=ReviewAction.RETURNED_FOR_REPROCESSING,
            previous_status=previous_status,
            new_status=HumanReviewStatus.UNREVIEWED,
            previous_value=previous_value,
            new_value=None,
            operator=operator,
            reviewed_at=timestamp,
            reason=reason.strip(),
        )
    )
    updated.status = TaskStatus.EXTRACTING
    updated.reviewed_by = None
    updated.reviewed_at = None
    updated.updated_at = timestamp
    updated.issues.append(f"字段 {field_name} 已退回重处理：{reason.strip()}")
    return updated


def summarize_review(task: DocumentTask) -> ReviewSummary:
    """汇总缺失、未复核字段及其尚未解决的校验提示。"""

    missing = tuple(name for name in CORE_FIELD_NAMES if name not in task.field_results)
    unreviewed = tuple(
        name for name in CORE_FIELD_NAMES
        if name in task.field_results
        and task.field_results[name].review_status == HumanReviewStatus.UNREVIEWED
    )
    unresolved_issues = tuple(
        (name, tuple(task.field_results[name].validation_issues))
        for name in unreviewed if task.field_results[name].validation_issues
    )
    ready = not missing and not unreviewed and task.status == TaskStatus.CONFIRMED
    return ReviewSummary(missing, unreviewed, unresolved_issues, ready)


def confirmed_values_for_excel(task: DocumentTask) -> dict[str, str | None]:
    """仅在全部字段明确复核后提供 Excel 可用最终值。"""

    summary = summarize_review(task)
    if not summary.ready_for_excel:
        raise ReviewIncompleteError(summary)
    return {name: task.field_results[name].final_value for name in CORE_FIELD_NAMES}


def _prepare_review(
    task: DocumentTask,
    field_name: str,
    operator: str,
    reviewed_at: datetime | None,
) -> tuple[DocumentTask, FieldResult, datetime]:
    if task.status not in (TaskStatus.PENDING_REVIEW, TaskStatus.CONFIRMED):
        raise ReviewError("只有待复核或已确认任务可以执行人工复核")
    if field_name not in CORE_FIELD_NAMES:
        raise ReviewError(f"未知核心字段: {field_name}")
    if field_name not in task.field_results:
        raise ReviewError(f"任务缺少字段: {field_name}")
    if not operator.strip():
        raise ReviewError("复核操作人不能为空")
    timestamp = reviewed_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ReviewError("复核时间必须包含时区")
    updated = deepcopy(task)
    return updated, updated.field_results[field_name], timestamp


def _record_field_review(
    task: DocumentTask,
    result: FieldResult,
    action: ReviewAction,
    new_status: HumanReviewStatus,
    new_value: str | None,
    operator: str,
    timestamp: datetime,
) -> DocumentTask:
    previous_status = result.review_status
    previous_value = result.final_value
    result.review_status = new_status
    result.final_value = new_value
    result.reviewed_by = operator
    result.reviewed_at = timestamp
    task.review_history.append(
        FieldReviewAudit(
            field_name=result.field_name,
            action=action,
            previous_status=previous_status,
            new_status=new_status,
            previous_value=previous_value,
            new_value=new_value,
            operator=operator,
            reviewed_at=timestamp,
        )
    )
    task.updated_at = timestamp
    _refresh_task_review_status(task, operator, timestamp)
    return task


def _refresh_task_review_status(task: DocumentTask, operator: str, timestamp: datetime) -> None:
    all_present = all(name in task.field_results for name in CORE_FIELD_NAMES)
    all_reviewed = all_present and all(
        task.field_results[name].review_status != HumanReviewStatus.UNREVIEWED
        for name in CORE_FIELD_NAMES
    )
    if all_reviewed:
        task.status = TaskStatus.CONFIRMED
        task.reviewed_by = operator
        task.reviewed_at = timestamp
    else:
        task.status = TaskStatus.PENDING_REVIEW
        task.reviewed_by = None
        task.reviewed_at = None
