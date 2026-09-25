"""从不可信上传到待人工复核状态的端到端应用编排。"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol

from app.file_intake import FileIntakeError, FileIntakeService
from app.ocr_service import OCREnrichmentResult, OCRPageTrace, OCRProcessingError, OCRProcessingService
from app.parsing_service import DocumentParsingService, ParsingResult
from app.state import DocumentTask, TaskRetryAudit, TaskStatus, TaskStatusAudit
from app.tools.document_parser import DocumentParseError, ParsedDocument


class ProcessingStage(StrEnum):
    INTAKE = "intake"
    PARSING = "parsing"
    OCR = "ocr"
    EXTRACTION = "extraction"


class FieldProcessingWorkflow(Protocol):
    def process(self, task: DocumentTask, document: ParsedDocument) -> DocumentTask:
        ...


@dataclass(frozen=True, slots=True)
class PipelineVersions:
    parser_version: str

    def __post_init__(self) -> None:
        if not self.parser_version.strip():
            raise ValueError("parser_version 不能为空")


@dataclass(frozen=True, slots=True)
class ProcessingOutcome:
    task: DocumentTask
    parsed_document: ParsedDocument | None = None
    ocr_traces: tuple[OCRPageTrace, ...] = ()
    is_duplicate: bool = False
    failed_stage: ProcessingStage | None = None
    ocr_page_numbers: tuple[int, ...] = ()


class DocumentProcessingPipeline:
    """协调接入、解析、OCR 和字段处理，同时保留可恢复阶段。"""

    def __init__(
        self,
        intake: FileIntakeService,
        parser: DocumentParsingService,
        field_workflow: FieldProcessingWorkflow,
        versions: PipelineVersions,
        ocr: OCRProcessingService | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._intake = intake
        self._parser = parser
        self._ocr = ocr
        self._field_workflow = field_workflow
        self._versions = versions
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def process(
        self,
        task_id: str,
        filename: str,
        content: bytes,
        *,
        declared_media_type: str | None = None,
        known_hashes: Mapping[str, str] | Collection[str] = (),
    ) -> ProcessingOutcome:
        task = DocumentTask(task_id=task_id)
        try:
            intake = self._intake.accept(
                filename,
                content,
                declared_media_type=declared_media_type,
                known_hashes=known_hashes,
            )
        except FileIntakeError as error:
            task.issues.append(f"文件接入失败：{error.code.value}")
            self._transition(task, TaskStatus.FAILED, error.code.value)
            return ProcessingOutcome(task, failed_stage=ProcessingStage.INTAKE)

        task.source_documents.append(intake.document)
        task.parser_version = self._versions.parser_version
        self._transition(task, TaskStatus.PARSING)
        try:
            parsing = self._parser.parse(intake.document, content)
        except DocumentParseError as error:
            task.issues.append(f"文档解析失败：{error.code.value}")
            self._transition(task, TaskStatus.FAILED, error.code.value)
            return ProcessingOutcome(
                task,
                is_duplicate=intake.is_duplicate,
                failed_stage=ProcessingStage.PARSING,
            )

        return self._continue_after_parsing(task, parsing, intake.is_duplicate)

    def retry(
        self, previous: ProcessingOutcome, *, content: bytes | None = None
    ) -> ProcessingOutcome:
        """从失败阶段恢复，复用此前已成功阶段的产物。"""

        if previous.task.status != TaskStatus.FAILED or previous.failed_stage is None:
            raise ValueError("只有带失败阶段的失败任务可以重试")
        if previous.failed_stage == ProcessingStage.INTAKE:
            raise ValueError("文件接入失败需修正文件后重新提交")

        task = deepcopy(previous.task)
        stage = previous.failed_stage
        self._record_retry(task, stage)
        self._transition(task, TaskStatus.PARSING, f"retry:{stage.value}")
        self._clear_retry_issues(task, stage)

        if stage == ProcessingStage.PARSING:
            if content is None:
                raise ValueError("重试解析阶段必须提供原文件内容")
            if len(task.source_documents) != 1:
                raise ValueError("当前恢复流程要求任务仅包含一个源文档")
            try:
                parsing = self._parser.parse(task.source_documents[0], content)
            except DocumentParseError as error:
                task.issues.append(f"文档解析重试失败：{error.code.value}")
                self._transition(task, TaskStatus.FAILED, error.code.value)
                return ProcessingOutcome(
                    task,
                    is_duplicate=previous.is_duplicate,
                    failed_stage=ProcessingStage.PARSING,
                )
            return self._continue_after_parsing(task, parsing, previous.is_duplicate)

        if previous.parsed_document is None:
            raise ValueError("恢复该阶段需要已解析文档")
        if stage == ProcessingStage.OCR:
            parsing = ParsingResult(
                previous.parsed_document, previous.ocr_page_numbers
            )
            return self._continue_after_parsing(task, parsing, previous.is_duplicate)

        completed = self._field_workflow.process(task, previous.parsed_document)
        failed_stage = (
            ProcessingStage.EXTRACTION if completed.status == TaskStatus.FAILED else None
        )
        return ProcessingOutcome(
            task=completed,
            parsed_document=previous.parsed_document,
            ocr_traces=previous.ocr_traces,
            is_duplicate=previous.is_duplicate,
            failed_stage=failed_stage,
            ocr_page_numbers=previous.ocr_page_numbers,
        )

    def _continue_after_parsing(
        self, task: DocumentTask, parsing: ParsingResult, is_duplicate: bool
    ) -> ProcessingOutcome:
        enriched = self._run_ocr(task, parsing)
        if isinstance(enriched, ProcessingOutcome):
            return ProcessingOutcome(
                task=enriched.task,
                parsed_document=enriched.parsed_document,
                ocr_traces=enriched.ocr_traces,
                is_duplicate=is_duplicate,
                failed_stage=enriched.failed_stage,
                ocr_page_numbers=parsing.ocr_page_numbers,
            )

        task.issues.extend(enriched.document.warnings)
        task.ocr_versions = tuple(
            sorted(
                {
                    f"{trace.engine}:{trace.engine_version}"
                    for trace in enriched.traces
                    if trace.succeeded and trace.engine and trace.engine_version
                }
            )
        )
        completed = self._field_workflow.process(task, enriched.document)
        failed_stage = (
            ProcessingStage.EXTRACTION if completed.status == TaskStatus.FAILED else None
        )
        return ProcessingOutcome(
            task=completed,
            parsed_document=enriched.document,
            ocr_traces=enriched.traces,
            is_duplicate=is_duplicate,
            failed_stage=failed_stage,
            ocr_page_numbers=parsing.ocr_page_numbers,
        )

    def _record_retry(self, task: DocumentTask, stage: ProcessingStage) -> None:
        retried_at = self._now_provider()
        if retried_at.tzinfo is None:
            raise ValueError("流程时间必须包含时区")
        attempt = 1 + sum(item.stage == stage.value for item in task.retry_history)
        task.retry_history.append(TaskRetryAudit(stage.value, attempt, retried_at))

    @staticmethod
    def _clear_retry_issues(task: DocumentTask, stage: ProcessingStage) -> None:
        prefixes = {
            ProcessingStage.PARSING: ("文档解析失败：", "文档解析重试失败："),
            ProcessingStage.OCR: ("OCR 处理失败：",),
            ProcessingStage.EXTRACTION: ("字段提取失败",),
        }[stage]
        task.issues = [
            issue for issue in task.issues if not issue.startswith(prefixes)
        ]

    def _run_ocr(
        self, task: DocumentTask, parsing: ParsingResult
    ) -> OCREnrichmentResult | ProcessingOutcome:
        if not parsing.requires_ocr:
            return OCREnrichmentResult(parsing.document, ())
        if self._ocr is None:
            task.issues.append("OCR 处理失败：not_configured")
            self._transition(task, TaskStatus.FAILED, "not_configured")
            return ProcessingOutcome(
                task,
                parsed_document=parsing.document,
                failed_stage=ProcessingStage.OCR,
            )
        try:
            return self._ocr.enrich(parsing)
        except OCRProcessingError as error:
            task.issues.append(f"OCR 处理失败：{error.code.value}")
            self._transition(task, TaskStatus.FAILED, error.code.value)
            return ProcessingOutcome(
                task,
                parsed_document=parsing.document,
                failed_stage=ProcessingStage.OCR,
            )

    def _transition(
        self, task: DocumentTask, new_status: TaskStatus, reason: str | None = None
    ) -> None:
        changed_at = self._now_provider()
        if changed_at.tzinfo is None:
            raise ValueError("流程时间必须包含时区")
        previous = task.status
        task.status = new_status
        task.updated_at = changed_at
        task.status_history.append(TaskStatusAudit(previous, new_status, changed_at, reason))
