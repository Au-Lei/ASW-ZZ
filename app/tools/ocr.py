"""供应商无关的 OCR 输入、输出与错误契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from app.tools.document_parser import TextRegion


class OCRErrorCode(StrEnum):
    TIMEOUT = "timeout"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INVALID_RESPONSE = "invalid_response"
    UNSUPPORTED_IMAGE = "unsupported_image"


class OCRError(Exception):
    def __init__(self, code: OCRErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class OCRTextRegion:
    region: TextRegion
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence 必须在 0 到 1 之间")


@dataclass(frozen=True, slots=True)
class OCRPageResult:
    page_number: int
    regions: tuple[OCRTextRegion, ...]
    engine: str
    engine_version: str
    request_id: str | None = None

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("page_number 必须从 1 开始")
        if not self.engine.strip() or not self.engine_version.strip():
            raise ValueError("OCR 引擎及版本不能为空")

    @property
    def text(self) -> str:
        return "\n".join(item.region.text for item in self.regions)


@runtime_checkable
class OCRProvider(Protocol):
    def recognize(self, page_number: int, image: bytes) -> OCRPageResult:
        """识别单页图像，不得补写图像中不存在的内容。"""

        ...
