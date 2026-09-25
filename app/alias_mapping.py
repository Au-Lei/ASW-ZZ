"""船公司、港口、场站与箱型共用的版本化别名映射。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum

from app.state import FieldResult


class EntityType(StrEnum):
    CARRIER = "carrier"
    CONTAINER_TYPE = "container_type"
    PORT = "port"
    YARD = "yard"


class MappingStatus(StrEnum):
    MATCHED = "matched"
    UNMAPPED = "unmapped"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class AliasMapping:
    entity_type: EntityType
    alias: str
    standard_value: str
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.alias.strip():
            raise ValueError("alias 不能为空")
        if not self.standard_value.strip():
            raise ValueError("standard_value 不能为空")


@dataclass(frozen=True, slots=True)
class MappingDecision:
    status: MappingStatus
    normalized_value: str | None = None


@dataclass(frozen=True, slots=True)
class MappingRegistry:
    version: str
    entries: tuple[AliasMapping, ...]

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("映射版本不能为空")

    def resolve(self, entity_type: EntityType, raw_value: str) -> MappingDecision:
        key = _comparison_key(raw_value)
        matches = [
            entry
            for entry in self.entries
            if entry.enabled
            and entry.entity_type == entity_type
            and _comparison_key(entry.alias) == key
        ]
        if not matches:
            return MappingDecision(MappingStatus.UNMAPPED)
        if len(matches) > 1:
            return MappingDecision(MappingStatus.AMBIGUOUS)
        return MappingDecision(MappingStatus.MATCHED, matches[0].standard_value)


def apply_alias_mapping(
    result: FieldResult,
    entity_type: EntityType,
    registry: MappingRegistry,
) -> FieldResult:
    """只在唯一启用映射命中时写入标准值。"""

    mapped = deepcopy(result)
    _clear_mapping_issues(mapped)
    mapped.normalized_value = None
    if mapped.raw_value is None or not mapped.raw_value.strip():
        return mapped

    decision = registry.resolve(entity_type, mapped.raw_value)
    if decision.status == MappingStatus.MATCHED:
        mapped.normalized_value = decision.normalized_value
    else:
        mapped.validation_issues.append(mapping_issue(entity_type, decision.status))
    return mapped


def mapping_issue(entity_type: EntityType, status: MappingStatus) -> str:
    label = {
        EntityType.CARRIER: "船公司",
        EntityType.CONTAINER_TYPE: "箱型",
        EntityType.PORT: "港口",
        EntityType.YARD: "场站",
    }[entity_type]
    if status == MappingStatus.UNMAPPED:
        return f"{label}未找到启用映射，需人工确认"
    if status == MappingStatus.AMBIGUOUS:
        return f"{label}存在多个启用映射，未自动标准化，需人工确认"
    raise ValueError("已匹配状态没有校验提示")


def _clear_mapping_issues(result: FieldResult) -> None:
    known_issues = {
        mapping_issue(entity_type, status)
        for entity_type in EntityType
        for status in (MappingStatus.UNMAPPED, MappingStatus.AMBIGUOUS)
    }
    result.validation_issues = [
        issue for issue in result.validation_issues if issue not in known_issues
    ]


def _comparison_key(value: str) -> str:
    return " ".join(value.split()).casefold()
