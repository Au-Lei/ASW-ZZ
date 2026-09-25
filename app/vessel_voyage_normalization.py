"""船名与航次的保守空白标准化规则。"""

from __future__ import annotations

from copy import deepcopy

from app.state import FieldResult


VESSEL_VOYAGE_FIELDS = ("vessel_name", "voyage")


def normalize_vessel_and_voyage(
    results: dict[str, FieldResult],
) -> dict[str, FieldResult]:
    """返回字段副本，仅规范空白，不改变具有业务含义的字符。"""

    normalized = deepcopy(results)
    for field_name in VESSEL_VOYAGE_FIELDS:
        result = normalized.get(field_name)
        if result is None:
            continue
        if result.field_name != field_name:
            raise ValueError(f"字段键与 FieldResult.field_name 不一致: {field_name}")

        result.normalized_value = _normalize_whitespace(result.raw_value)
    return normalized


def _normalize_whitespace(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None
