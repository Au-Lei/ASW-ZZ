"""文档解析器的通用接口和返回数据结构。

具体的 PDF、图片或办公文档解析器将在后续实现。本模块只定义各解析器
都必须遵守的契约，使上层流程不依赖某一个第三方解析库。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.state import SourceDocument


class DocumentParseErrorCode(StrEnum):
    """可持久化和安全展示的解析错误类别。"""

    UNSUPPORTED = "unsupported"
    DAMAGED = "damaged"
    ENCRYPTED = "encrypted"
    TOO_MANY_PAGES = "too_many_pages"
    PARSER_FAILURE = "parser_failure"
    CONTRACT_VIOLATION = "contract_violation"


class DocumentParseError(Exception):
    """文档无法按解析器契约处理时抛出的分类异常。"""

    def __init__(self, code: DocumentParseErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TextRegion:
    """页面内一段可定位文字；坐标采用解析器原始页面坐标。"""

    text: str
    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("区域文字不能为空")
        if min(self.left, self.top, self.right, self.bottom) < 0:
            raise ValueError("区域坐标不能小于 0")
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("区域右下坐标必须大于左上坐标")


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """单页解析结果。

    空文本是合法结果，例如扫描页在 OCR 之前可能暂时没有可用文字。
    """

    page_number: int
    text: str
    regions: tuple[TextRegion, ...] = ()

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("page_number 必须从 1 开始")


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """保留页级边界的文档解析结果。"""

    document_id: str
    pages: tuple[ParsedPage, ...]
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.document_id.strip():
            raise ValueError("document_id 不能为空")
        if not self.pages:
            raise ValueError("解析结果必须至少包含一页")

        page_numbers = tuple(page.page_number for page in self.pages)
        if page_numbers != tuple(sorted(page_numbers)):
            raise ValueError("pages 必须按页码升序排列")
        if len(page_numbers) != len(set(page_numbers)):
            raise ValueError("pages 不能包含重复页码")


@runtime_checkable
class DocumentParser(Protocol):
    """所有具体文档解析器必须实现的接口。"""

    def supports(self, media_type: str) -> bool:
        """返回解析器是否支持指定的 MIME 类型。"""

        ...

    def parse(
        self,
        document: SourceDocument,
        content: bytes,
    ) -> ParsedDocument:
        """解析文件内容，并保留页级文字与警告。

        实现不得根据业务常识补写原文中不存在的内容。无法处理文件时应
        抛出 :class:`DocumentParseError` 或其子类。
        """

        ...
