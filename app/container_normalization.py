"""箱量及箱型的保守解析与标准化规则。"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass

from app.state import FieldResult


CONTAINER_UNSUPPORTED_FORMAT_ISSUE = "箱量及类型格式无法明确解析，需人工确认"
_ITEM_PATTERN = re.compile(
    r"^(?P<quantity>[1-9]\d*)\s*[xX×*]\s*"
    r"(?P<size>20|40|45)\s*'?\s*(?P<type_code>[A-Za-z]{2,4})$"
)
_SEPARATOR_PATTERN = re.compile(r"\s*(?:\+|,|，|;|；)\s*")


@dataclass(frozen=True, slots=True)
class ContainerItem:
    """一个明确解析出的箱量、尺寸与箱型。"""

    quantity: int
    size: int
    type_code: str


@dataclass(frozen=True, slots=True)
class ContainerNormalizationResult:
    """保留字段展示结果及独立结构化明细。"""

    field_result: FieldResult
    items: tuple[ContainerItem, ...]


def normalize_container_summary(result: FieldResult) -> ContainerNormalizationResult:
    """仅解析完整明确的箱量表达式，不补写数量、尺寸或箱型。"""

    if result.field_name != "container_summary":
        raise ValueError("normalize_container_summary 只能处理 container_summary 字段")

    normalized = deepcopy(result)
    normalized.normalized_value = None
    normalized.validation_issues = [
        issue
        for issue in normalized.validation_issues
        if issue != CONTAINER_UNSUPPORTED_FORMAT_ISSUE
    ]
    if normalized.raw_value is None:
        return ContainerNormalizationResult(normalized, ())

    raw_value = normalized.raw_value.strip()
    if not raw_value:
        return ContainerNormalizationResult(normalized, ())

    items = _parse_items(raw_value)
    if items is None:
        normalized.validation_issues.append(CONTAINER_UNSUPPORTED_FORMAT_ISSUE)
        return ContainerNormalizationResult(normalized, ())

    normalized.normalized_value = " + ".join(
        f"{item.quantity}X{item.size}{item.type_code}" for item in items
    )
    return ContainerNormalizationResult(normalized, items)


def _parse_items(value: str) -> tuple[ContainerItem, ...] | None:
    parts = _SEPARATOR_PATTERN.split(value)
    if not parts or any(not part for part in parts):
        return None

    items: list[ContainerItem] = []
    for part in parts:
        match = _ITEM_PATTERN.fullmatch(part)
        if match is None:
            return None
        items.append(
            ContainerItem(
                quantity=int(match.group("quantity")),
                size=int(match.group("size")),
                type_code=match.group("type_code").upper(),
            )
        )
    return tuple(items)
