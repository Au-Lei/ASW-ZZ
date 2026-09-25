"""从任务审计记录汇总不含业务正文的处理指标。"""

from __future__ import annotations

from dataclasses import dataclass

from app.state import DocumentTask, ReviewAction, TaskStatus

_SAFE_ERROR_CODES = {
    "empty", "too_large", "unsupported", "type_mismatch", "damaged",
    "encrypted", "unsafe_content", "too_many_pages", "parser_failure",
    "contract_violation", "not_configured", "timeout", "service_unavailable",
    "invalid_response", "unsupported_image",
}


@dataclass(frozen=True, slots=True)
class StageDuration:
    stage: TaskStatus
    seconds: float


@dataclass(frozen=True, slots=True)
class TaskMetrics:
    task_id: str
    status: TaskStatus
    stage_durations: tuple[StageDuration, ...]
    error_codes: tuple[str, ...]
    parser_version: str | None
    ocr_versions: tuple[str, ...]
    extractor_version: str | None
    prompt_version: str | None
    ruleset_version: str | None
    mapping_version: str | None
    reviewed_fields: int
    modified_fields: int
    modification_rate: float | None


def summarize_task_metrics(task: DocumentTask) -> TaskMetrics:
    """计算阶段耗时与最终人工修改比例。无审计时不推测耗时。"""

    durations: list[StageDuration] = []
    transitions = task.status_history
    for current, following in zip(transitions, transitions[1:]):
        elapsed = (following.changed_at - current.changed_at).total_seconds()
        if elapsed >= 0:
            durations.append(StageDuration(current.new_status, elapsed))
    if transitions and task.updated_at > transitions[-1].changed_at:
        elapsed = (task.updated_at - transitions[-1].changed_at).total_seconds()
        durations.append(StageDuration(transitions[-1].new_status, elapsed))

    error_codes = tuple(
        audit.reason
        for audit in transitions
        if audit.new_status == TaskStatus.FAILED
        and audit.reason is not None
        and audit.reason in _SAFE_ERROR_CODES
    )
    latest_actions: dict[str, ReviewAction] = {}
    for audit in task.review_history:
        latest_actions[audit.field_name] = audit.action
    reviewed = sum(
        action in {ReviewAction.ACCEPTED, ReviewAction.MODIFIED, ReviewAction.CONFIRMED_EMPTY}
        for action in latest_actions.values()
    )
    modified = sum(action == ReviewAction.MODIFIED for action in latest_actions.values())
    return TaskMetrics(
        task_id=task.task_id,
        status=task.status,
        stage_durations=tuple(durations),
        error_codes=error_codes,
        parser_version=task.parser_version,
        ocr_versions=task.ocr_versions,
        extractor_version=task.extractor_version,
        prompt_version=task.prompt_version,
        ruleset_version=task.ruleset_version,
        mapping_version=task.mapping_version,
        reviewed_fields=reviewed,
        modified_fields=modified,
        modification_rate=modified / reviewed if reviewed else None,
    )
