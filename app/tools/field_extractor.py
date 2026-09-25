"""字段提取器的供应商无关契约。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.state import FieldResult
from app.tools.document_parser import ParsedDocument


class FieldExtractionError(Exception):
    """字段提取无法按契约完成时抛出的基础异常。"""


@runtime_checkable
class FieldExtractor(Protocol):
    """将已解析文档转换为可复核字段结果的通用接口。"""

    def extract(self, document: ParsedDocument) -> dict[str, FieldResult]:
        """返回以字段名为键的提取结果。

        实现只能返回文档中有依据的值；无法确定的字段必须保留空值，
        不得使用业务常识或外部信息补写。
        """

        ...
