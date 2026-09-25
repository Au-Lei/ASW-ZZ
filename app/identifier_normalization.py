"""Booking No. 与合约号的保守字符串标准化及冲突提示。"""

from __future__ import annotations

from copy import deepcopy

from app.state import FieldResult


IDENTIFIER_FIELDS = ("booking_no", "contract_no")
MULTIPLE_IDENTIFIER_CANDIDATES_ISSUE = (
    "编号存在多个候选，禁止按长度或格式自动归类，需人工确认"
)
CROSS_FIELD_IDENTIFIER_CONFLICT_ISSUE = (
    "Booking No. 与合约号存在相同编号候选，需人工确认归属"
)


def normalize_identifiers(
    results: dict[str, FieldResult],
) -> dict[str, FieldResult]:
    """返回编号字段副本，仅去除首尾空白并标记可疑归属。"""

    normalized = deepcopy(results)
    for field_name in IDENTIFIER_FIELDS:
        result = normalized.get(field_name)
        if result is None:
            continue
        if result.field_name != field_name:
            raise ValueError(f"字段键与 FieldResult.field_name 不一致: {field_name}")

        _remove_identifier_issues(result)
        stripped_value = result.raw_value.strip() if result.raw_value is not None else ""
        result.normalized_value = stripped_value or None
        if len(result.candidates) > 1:
            result.validation_issues.append(MULTIPLE_IDENTIFIER_CANDIDATES_ISSUE)

    booking = normalized.get("booking_no")
    contract = normalized.get("contract_no")
    if booking is not None and contract is not None:
        booking_values = _possible_values(booking)
        contract_values = _possible_values(contract)
        if booking_values & contract_values:
            booking.validation_issues.append(CROSS_FIELD_IDENTIFIER_CONFLICT_ISSUE)
            contract.validation_issues.append(CROSS_FIELD_IDENTIFIER_CONFLICT_ISSUE)

    return normalized


def _possible_values(result: FieldResult) -> set[str]:
    values = {candidate.value.strip() for candidate in result.candidates}
    if result.raw_value is not None:
        values.add(result.raw_value.strip())
    return {value for value in values if value}


def _remove_identifier_issues(result: FieldResult) -> None:
    identifier_issues = {
        MULTIPLE_IDENTIFIER_CANDIDATES_ISSUE,
        CROSS_FIELD_IDENTIFIER_CONFLICT_ISSUE,
    }
    result.validation_issues = [
        issue for issue in result.validation_issues if issue not in identifier_issues
    ]
