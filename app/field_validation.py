"""不依赖业务字典的第一批确定性字段问题标记规则。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from app.extraction_schema import CORE_FIELD_NAMES
from app.state import FieldResult


MISSING_VALUE_ISSUE = "字段缺失，需人工确认"
MISSING_CONFIDENCE_ISSUE = "字段未提供置信度，需人工确认"
LOW_CONFIDENCE_ISSUE = "字段置信度低，需人工确认"
MULTIPLE_CANDIDATES_ISSUE = "检测到多个候选值，需人工确认"


@dataclass(frozen=True, slots=True)
class FieldValidationPolicy:
    """字段提示规则配置；置信度阈值必须由调用方明确提供。"""

    low_confidence_threshold: float
    optional_fields: frozenset[str] = field(
        default_factory=lambda: frozenset({"salesperson", "customer_service"})
    )

    def __post_init__(self) -> None:
        threshold = self.low_confidence_threshold
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not 0 <= threshold <= 1
        ):
            raise ValueError("low_confidence_threshold 必须在 0 到 1 之间")
        unknown_optional_fields = self.optional_fields - set(CORE_FIELD_NAMES)
        if unknown_optional_fields:
            names = ", ".join(sorted(unknown_optional_fields))
            raise ValueError(f"optional_fields 包含未知字段: {names}")


def assess_field_results(
    results: dict[str, FieldResult],
    policy: FieldValidationPolicy,
) -> dict[str, FieldResult]:
    """返回带确定性提示的副本，不覆盖提取值或调用方原始数据。"""

    assessed = deepcopy(results)
    for field_name, result in assessed.items():
        has_value = result.raw_value is not None
        has_candidates = bool(result.candidates)

        if not has_value and not has_candidates and field_name not in policy.optional_fields:
            _append_once(result.validation_issues, MISSING_VALUE_ISSUE)

        if has_value and result.confidence is None:
            _append_once(result.validation_issues, MISSING_CONFIDENCE_ISSUE)
        elif (
            has_value
            and result.confidence is not None
            and result.confidence < policy.low_confidence_threshold
        ):
            _append_once(result.validation_issues, LOW_CONFIDENCE_ISSUE)

        if len(result.candidates) > 1:
            _append_once(result.validation_issues, MULTIPLE_CANDIDATES_ISSUE)

    return assessed


def _append_once(issues: list[str], issue: str) -> None:
    if issue not in issues:
        issues.append(issue)
