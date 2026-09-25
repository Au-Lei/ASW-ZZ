"""船期字段的保守日期标准化规则。"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date

from app.state import FieldResult


DATE_YEAR_MISSING_ISSUE = "船期缺少年份，未自动补全，需人工确认"
DATE_INVALID_ISSUE = "船期日期无效，需人工确认"
DATE_UNSUPPORTED_FORMAT_ISSUE = "船期格式不受支持，未自动转换，需人工确认"

_YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_YEAR_FIRST_PATTERN = re.compile(
    r"^(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})$"
)
_CHINESE_PATTERN = re.compile(
    r"^(?P<year>\d{4})年\s*(?P<month>\d{1,2})月\s*(?P<day>\d{1,2})日?$"
)
_DAY_MONTH_NAME_PATTERN = re.compile(
    r"^(?P<day>\d{1,2})[\s-]+(?P<month_name>[A-Za-z]{3,9})[\s,-]+(?P<year>\d{4})$"
)
_MONTH_NAME_DAY_PATTERN = re.compile(
    r"^(?P<month_name>[A-Za-z]{3,9})[\s-]+(?P<day>\d{1,2}),?[\s]+(?P<year>\d{4})$"
)
_MONTHS = {
    "JAN": 1,
    "JANUARY": 1,
    "FEB": 2,
    "FEBRUARY": 2,
    "MAR": 3,
    "MARCH": 3,
    "APR": 4,
    "APRIL": 4,
    "MAY": 5,
    "JUN": 6,
    "JUNE": 6,
    "JUL": 7,
    "JULY": 7,
    "AUG": 8,
    "AUGUST": 8,
    "SEP": 9,
    "SEPT": 9,
    "SEPTEMBER": 9,
    "OCT": 10,
    "OCTOBER": 10,
    "NOV": 11,
    "NOVEMBER": 11,
    "DEC": 12,
    "DECEMBER": 12,
}
_DATE_ISSUES = {
    DATE_YEAR_MISSING_ISSUE,
    DATE_INVALID_ISSUE,
    DATE_UNSUPPORTED_FORMAT_ISSUE,
}


def normalize_etd(result: FieldResult) -> FieldResult:
    """返回标准化后的副本；没有明确年份时绝不推断年份。"""

    if result.field_name != "etd":
        raise ValueError("normalize_etd 只能处理 etd 字段")

    normalized = deepcopy(result)
    normalized.normalized_value = None
    normalized.validation_issues = [
        issue for issue in normalized.validation_issues if issue not in _DATE_ISSUES
    ]
    if normalized.raw_value is None:
        return normalized

    raw_value = normalized.raw_value.strip()
    if not _YEAR_PATTERN.search(raw_value):
        normalized.validation_issues.append(DATE_YEAR_MISSING_ISSUE)
        return normalized

    parts = _parse_supported_date(raw_value)
    if parts is None:
        normalized.validation_issues.append(DATE_UNSUPPORTED_FORMAT_ISSUE)
        return normalized

    year, month, day = parts
    try:
        normalized.normalized_value = date(year, month, day).isoformat()
    except ValueError:
        normalized.validation_issues.append(DATE_INVALID_ISSUE)
    return normalized


def _parse_supported_date(value: str) -> tuple[int, int, int] | None:
    for pattern in (_YEAR_FIRST_PATTERN, _CHINESE_PATTERN):
        match = pattern.fullmatch(value)
        if match:
            return (
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )

    for pattern in (_DAY_MONTH_NAME_PATTERN, _MONTH_NAME_DAY_PATTERN):
        match = pattern.fullmatch(value)
        if match:
            month = _MONTHS.get(match.group("month_name").upper())
            if month is None:
                return None
            return int(match.group("year")), month, int(match.group("day"))
    return None
