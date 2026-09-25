"""业务处理过程中共享的数据模型。

本模块只描述数据，不负责文件解析、AI 提取、字段标准化、持久化或
流程编排。后续节点应通过这些模型交换数据，避免覆盖原始提取结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class TaskStatus(StrEnum):
    """文档任务所处的处理阶段。"""

    UPLOADED = "uploaded"
    PARSING = "parsing"
    EXTRACTING = "extracting"
    NORMALIZING = "normalizing"
    PENDING_REVIEW = "pending_review"
    CONFIRMED = "confirmed"
    GENERATING_EXCEL = "generating_excel"
    COMPLETED = "completed"
    FAILED = "failed"


class HumanReviewStatus(StrEnum):
    """人工对单个字段的复核结果。"""

    UNREVIEWED = "unreviewed"
    ACCEPTED = "accepted"
    MODIFIED = "modified"
    CONFIRMED_EMPTY = "confirmed_empty"


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    """字段值在原始文档中的可追溯证据。

    ``page_number`` 从 1 开始。暂时不定义版面坐标类型，待解析器选型后再
    扩展，避免当前模型提前绑定某个 PDF/OCR 工具。
    """

    document_id: str
    page_number: int | None = None
    text_excerpt: str | None = None

    def __post_init__(self) -> None:
        if not self.document_id.strip():
            raise ValueError("document_id 不能为空")
        if self.page_number is not None and self.page_number < 1:
            raise ValueError("page_number 必须从 1 开始")


@dataclass(frozen=True, slots=True)
class FieldCandidate:
    """存在歧义时保留的一个字段候选值。"""

    value: str
    confidence: float | None = None
    evidence: tuple[SourceEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("候选值不能为空")
        _validate_confidence(self.confidence)


@dataclass(slots=True)
class FieldResult:
    """单个业务字段从提取到人工确认的完整状态。

    原始值、标准化值与人工最终值分别保存，任何处理步骤都不应通过覆盖
    前一阶段的值来丢失溯源信息。
    """

    field_name: str
    raw_value: str | None = None
    normalized_value: str | None = None
    confidence: float | None = None
    evidence: list[SourceEvidence] = field(default_factory=list)
    candidates: list[FieldCandidate] = field(default_factory=list)
    validation_issues: list[str] = field(default_factory=list)
    review_status: HumanReviewStatus = HumanReviewStatus.UNREVIEWED
    final_value: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.field_name.strip():
            raise ValueError("field_name 不能为空")
        _validate_confidence(self.confidence)


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """输入文档的基本信息及受控存储引用。"""

    document_id: str
    filename: str
    media_type: str | None = None
    content_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.document_id.strip():
            raise ValueError("document_id 不能为空")
        if not self.filename.strip():
            raise ValueError("filename 不能为空")


@dataclass(slots=True)
class DocumentTask:
    """一次文档处理任务的聚合状态。"""

    task_id: str
    status: TaskStatus = TaskStatus.UPLOADED
    source_documents: list[SourceDocument] = field(default_factory=list)
    field_results: dict[str, FieldResult] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    extractor_version: str | None = None
    prompt_version: str | None = None
    ruleset_version: str | None = None
    template_version: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id 不能为空")


def _validate_confidence(confidence: float | None) -> None:
    """校验可选置信度，统一使用闭区间 0 到 1。"""

    if confidence is not None and not 0 <= confidence <= 1:
        raise ValueError("confidence 必须在 0 到 1 之间")
