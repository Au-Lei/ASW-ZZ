"""解析器选择、结果契约校验、页数限制与 OCR 分流。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.state import SourceDocument
from app.tools.document_parser import (
    DocumentParseError,
    DocumentParseErrorCode,
    DocumentParser,
    ParsedDocument,
)


@dataclass(frozen=True, slots=True)
class ParsingPolicy:
    max_pages: int = 100
    min_embedded_text_characters: int = 20

    def __post_init__(self) -> None:
        if self.max_pages < 1:
            raise ValueError("max_pages 必须大于 0")
        if self.min_embedded_text_characters < 1:
            raise ValueError("min_embedded_text_characters 必须大于 0")


@dataclass(frozen=True, slots=True)
class ParsingResult:
    document: ParsedDocument
    ocr_page_numbers: tuple[int, ...]

    @property
    def requires_ocr(self) -> bool:
        return bool(self.ocr_page_numbers)


class DocumentParsingService:
    """把已通过接入校验的文件交给唯一匹配的解析器。"""

    def __init__(
        self,
        parsers: Iterable[DocumentParser],
        policy: ParsingPolicy | None = None,
    ) -> None:
        self._parsers = tuple(parsers)
        if not self._parsers:
            raise ValueError("至少需要一个文档解析器")
        self.policy = policy or ParsingPolicy()

    def parse(self, source: SourceDocument, content: bytes) -> ParsingResult:
        if not source.media_type:
            raise DocumentParseError(
                DocumentParseErrorCode.UNSUPPORTED, "文件缺少已验证的媒体类型"
            )
        matches = tuple(parser for parser in self._parsers if parser.supports(source.media_type))
        if not matches:
            raise DocumentParseError(
                DocumentParseErrorCode.UNSUPPORTED, "没有可处理该文件类型的解析器"
            )
        if len(matches) > 1:
            raise DocumentParseError(
                DocumentParseErrorCode.CONTRACT_VIOLATION, "多个解析器声明支持同一文件类型"
            )

        try:
            parsed = matches[0].parse(source, content)
        except DocumentParseError:
            raise
        except Exception as exc:
            raise DocumentParseError(
                DocumentParseErrorCode.PARSER_FAILURE, "文档解析失败"
            ) from exc

        if parsed.document_id != source.document_id:
            raise DocumentParseError(
                DocumentParseErrorCode.CONTRACT_VIOLATION, "解析结果的文档标识不匹配"
            )
        if len(parsed.pages) > self.policy.max_pages:
            raise DocumentParseError(
                DocumentParseErrorCode.TOO_MANY_PAGES,
                f"文档超过 {self.policy.max_pages} 页限制",
            )
        ocr_pages = tuple(
            page.page_number
            for page in parsed.pages
            if len("".join(page.text.split())) < self.policy.min_embedded_text_characters
        )
        return ParsingResult(parsed, ocr_pages)
