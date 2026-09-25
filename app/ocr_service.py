"""按需执行页级 OCR，并将结果合并回解析文档。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.parsing_service import ParsingResult
from app.tools.document_parser import ParsedDocument, ParsedPage
from app.tools.ocr import OCRError, OCRErrorCode, OCRPageResult, OCRProvider


@runtime_checkable
class PageImageRenderer(Protocol):
    """把指定文档页转换为 OCR 服务可接收的图像。"""

    def render(self, document_id: str, page_number: int) -> bytes:
        ...


@dataclass(frozen=True, slots=True)
class OCRPolicy:
    max_attempts: int = 2
    allow_partial_failure: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts 必须大于 0")


@dataclass(frozen=True, slots=True)
class OCRPageTrace:
    page_number: int
    attempts: int
    succeeded: bool
    error_code: OCRErrorCode | None = None
    engine: str | None = None
    engine_version: str | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class OCREnrichmentResult:
    document: ParsedDocument
    traces: tuple[OCRPageTrace, ...]

    @property
    def failed_page_numbers(self) -> tuple[int, ...]:
        return tuple(trace.page_number for trace in self.traces if not trace.succeeded)


class OCRProcessingError(Exception):
    """严格模式下任一 OCR 页失败时抛出的安全错误。"""

    def __init__(self, page_number: int, code: OCRErrorCode) -> None:
        super().__init__(f"第 {page_number} 页 OCR 处理失败：{code.value}")
        self.page_number = page_number
        self.code = code


class OCRProcessingService:
    _RETRYABLE = {OCRErrorCode.TIMEOUT, OCRErrorCode.SERVICE_UNAVAILABLE}

    def __init__(
        self,
        provider: OCRProvider,
        renderer: PageImageRenderer,
        policy: OCRPolicy | None = None,
    ) -> None:
        self._provider = provider
        self._renderer = renderer
        self.policy = policy or OCRPolicy()

    def enrich(self, parsing: ParsingResult) -> OCREnrichmentResult:
        target_pages = set(parsing.ocr_page_numbers)
        available_pages = {page.page_number for page in parsing.document.pages}
        if not target_pages <= available_pages:
            raise ValueError("OCR 页码不属于解析文档")

        pages: list[ParsedPage] = []
        traces: list[OCRPageTrace] = []
        warnings = list(parsing.document.warnings)
        for page in parsing.document.pages:
            if page.page_number not in target_pages:
                pages.append(page)
                continue
            result, trace = self._recognize_page(
                parsing.document.document_id, page.page_number
            )
            traces.append(trace)
            if result is None:
                if not self.policy.allow_partial_failure:
                    raise OCRProcessingError(page.page_number, trace.error_code or OCRErrorCode.INVALID_RESPONSE)
                warnings.append(
                    f"第 {page.page_number} 页 OCR 未完成（{trace.error_code.value if trace.error_code else 'unknown'}）"
                )
                pages.append(page)
                continue
            pages.append(_merge_page(page, result))

        return OCREnrichmentResult(
            ParsedDocument(
                document_id=parsing.document.document_id,
                pages=tuple(pages),
                warnings=tuple(warnings),
            ),
            tuple(traces),
        )

    def _recognize_page(
        self, document_id: str, page_number: int
    ) -> tuple[OCRPageResult | None, OCRPageTrace]:
        try:
            image = self._renderer.render(document_id, page_number)
            if not image:
                raise OCRError(OCRErrorCode.UNSUPPORTED_IMAGE, "页面图像为空")
        except OCRError as error:
            return None, OCRPageTrace(page_number, 0, False, error.code)
        except Exception:
            return None, OCRPageTrace(
                page_number, 0, False, OCRErrorCode.UNSUPPORTED_IMAGE
            )

        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                result = self._provider.recognize(page_number, image)
                if result.page_number != page_number:
                    raise OCRError(OCRErrorCode.INVALID_RESPONSE, "OCR 页码不匹配")
                return result, OCRPageTrace(
                    page_number=page_number,
                    attempts=attempt,
                    succeeded=True,
                    engine=result.engine,
                    engine_version=result.engine_version,
                    request_id=result.request_id,
                )
            except OCRError as error:
                if error.code not in self._RETRYABLE or attempt == self.policy.max_attempts:
                    return None, OCRPageTrace(page_number, attempt, False, error.code)
            except Exception:
                return None, OCRPageTrace(
                    page_number, attempt, False, OCRErrorCode.INVALID_RESPONSE
                )
        raise AssertionError("OCR 重试循环意外结束")


def _merge_page(page: ParsedPage, result: OCRPageResult) -> ParsedPage:
    ocr_text = result.text.strip()
    original_text = page.text.strip()
    combined = "\n".join(value for value in (original_text, ocr_text) if value)
    regions = page.regions + tuple(item.region for item in result.regions)
    return ParsedPage(page.page_number, combined, regions)
