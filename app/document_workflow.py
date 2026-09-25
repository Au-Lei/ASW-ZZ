"""从已解析文档到待人工复核任务的应用编排。"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone

from app.extraction_schema import CORE_FIELD_NAMES
from app.normalization_pipeline import NormalizationPipeline
from app.state import DocumentTask, FieldResult, TaskStatus, TaskStatusAudit
from app.tools.ai_field_extractor import ManualReviewRequired
from app.tools.document_parser import ParsedDocument
from app.tools.field_extractor import FieldExtractionError, FieldExtractor


class DocumentWorkflowError(Exception):
    """编排输入或状态不符合处理契约。"""


@dataclass(frozen=True, slots=True)
class ProcessingVersions:
    extractor_version: str
    prompt_version: str

    def __post_init__(self) -> None:
        if not self.extractor_version.strip():
            raise ValueError("extractor_version 不能为空")
        if not self.prompt_version.strip():
            raise ValueError("prompt_version 不能为空")


class DocumentWorkflow:
    """串联字段提取、确定性标准化和待复核状态。"""

    def __init__(
        self,
        extractor: FieldExtractor,
        normalizer: NormalizationPipeline,
        versions: ProcessingVersions,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._extractor = extractor
        self._normalizer = normalizer
        self._versions = versions
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def process(
        self,
        task: DocumentTask,
        document: ParsedDocument,
    ) -> DocumentTask:
        self._validate_input(task, document)
        updated = deepcopy(task)
        updated.extractor_version = self._versions.extractor_version
        updated.prompt_version = self._versions.prompt_version
        self._transition(updated, TaskStatus.EXTRACTING)

        try:
            extracted = self._extractor.extract(document)
            _validate_field_collection(extracted)
        except ManualReviewRequired as error:
            updated.field_results = {
                name: FieldResult(field_name=name) for name in CORE_FIELD_NAMES
            }
            updated.issues.append(
                "自动提取未可靠完成，已转人工处理："
                f"{error.reason}（尝试 {error.attempts} 次）"
            )
            self._transition(updated, TaskStatus.PENDING_REVIEW, error.reason)
            return updated
        except FieldExtractionError:
            updated.issues.append("字段提取失败，需检查提取器契约或服务状态")
            self._transition(updated, TaskStatus.FAILED, "字段提取失败")
            return updated

        self._transition(updated, TaskStatus.NORMALIZING)
        normalized = self._normalizer.normalize(extracted)
        updated.field_results = normalized.field_results
        updated.ruleset_version = normalized.ruleset_version
        updated.mapping_version = normalized.mapping_version
        self._transition(updated, TaskStatus.PENDING_REVIEW)
        return updated

    def _validate_input(
        self,
        task: DocumentTask,
        document: ParsedDocument,
    ) -> None:
        if task.status not in (TaskStatus.UPLOADED, TaskStatus.PARSING):
            raise DocumentWorkflowError("只有已上传或解析中任务可以进入文档处理流程")
        source_ids = {source.document_id for source in task.source_documents}
        if document.document_id not in source_ids:
            raise DocumentWorkflowError("解析文档不属于当前任务")

    def _transition(
        self,
        task: DocumentTask,
        new_status: TaskStatus,
        reason: str | None = None,
    ) -> None:
        changed_at = self._now_provider()
        if changed_at.tzinfo is None:
            raise DocumentWorkflowError("流程时间必须包含时区")
        previous_status = task.status
        task.status = new_status
        task.updated_at = changed_at
        task.status_history.append(
            TaskStatusAudit(previous_status, new_status, changed_at, reason)
        )


def _validate_field_collection(results: dict[str, FieldResult]) -> None:
    expected = set(CORE_FIELD_NAMES)
    actual = set(results)
    if actual != expected:
        raise FieldExtractionError("提取结果字段集合不完整")
    for field_name, result in results.items():
        if result.field_name != field_name:
            raise FieldExtractionError("提取结果字段键与字段名不一致")
