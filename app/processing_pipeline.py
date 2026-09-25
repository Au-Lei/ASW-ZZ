"""从不可信上传到待人工复核状态的端到端应用编排。"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol

from app.file_intake import FileIntakeError, FileIntakeService
from app.ocr_service import OCREnrichmentResult, OCRPageTrace, OCRProcessingError, OCRProcessingService
from app.parsing_service import DocumentParsingService, ParsingResult
from app.state import DocumentTask, TaskStatus, TaskStatusAudit
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

        enriched = self._run_ocr(task, parsing)
        if isinstance(enriched, ProcessingOutcome):
            return ProcessingOutcome(
                task=enriched.task,
                parsed_document=enriched.parsed_document,
                ocr_traces=enriched.ocr_traces,
                is_duplicate=intake.is_duplicate,
                failed_stage=enriched.failed_stage,
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
            is_duplicate=intake.is_duplicate,
            failed_stage=failed_stage,
        )

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
