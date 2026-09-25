"""供应商无关的 AI 字段提取适配器。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from app.extraction_prompt import PROMPT_VERSION, build_extraction_prompt
from app.extraction_schema import validate_extraction_results
from app.state import FieldCandidate, FieldResult, SourceEvidence
from app.tools.document_parser import ParsedDocument
from app.tools.field_extractor import FieldExtractionError


EXTRACTOR_VERSION = "schema-validating-field-extractor-v1"
AIParameterValue = str | int | float | bool | None
_SENSITIVE_PARAMETER_PARTS = ("key", "token", "secret", "password", "credential", "authorization")


@dataclass(frozen=True, slots=True)
class AITextResponse:
    """AI 客户端返回的文本及非敏感调用元数据。"""

    text: str
    service: str
    model: str
    parameters: tuple[tuple[str, AIParameterValue], ...] = ()
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not self.service.strip():
            raise ValueError("service 不能为空")
        if not self.model.strip():
            raise ValueError("model 不能为空")
        parameter_names = tuple(name for name, _ in self.parameters)
        if any(not name.strip() for name in parameter_names):
            raise ValueError("参数名不能为空")
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("参数名不能重复")
        if any(any(part in name.casefold() for part in _SENSITIVE_PARAMETER_PARTS) for name in parameter_names):
            raise ValueError("调用参数不能包含认证信息")
        if self.request_id is not None and not self.request_id.strip():
            raise ValueError("request_id 不能为空字符串")


@dataclass(frozen=True, slots=True)
class AIExtractionTrace:
    """一次提取调用的可审计元数据，不包含提示词或业务正文。"""

    document_id: str
    service: str
    model: str
    parameters: tuple[tuple[str, AIParameterValue], ...]
    request_id: str | None
    prompt_version: str
    extractor_version: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class ExtractionPolicy:
    """AI 提取的有限尝试与单次调用超时配置。"""

    max_attempts: int = 2
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or self.max_attempts < 1
        ):
            raise ValueError("max_attempts 必须是大于等于 1 的整数")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds 必须大于 0")


class AIClientError(Exception):
    """AI 客户端可分类错误。"""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class AIClientTimeoutError(AIClientError):
    """单次 AI 调用超过适配器传入的时限。"""

    def __init__(self, message: str = "AI 调用超时") -> None:
        super().__init__(message, retryable=True)


class ManualReviewRequired(FieldExtractionError):
    """自动提取无法可靠完成，需要转人工处理。"""

    def __init__(self, reason: str, attempts: int) -> None:
        super().__init__(f"AI 提取未可靠完成，需转人工处理: {reason}")
        self.reason = reason
        self.attempts = attempts


@runtime_checkable
class AITextClient(Protocol):
    """能够返回纯文本响应的最小 AI 客户端接口。"""

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float,
    ) -> AITextResponse:
        """在指定单次超时内返回模型文本。"""

        ...


class SchemaValidatingFieldExtractor:
    """调用 AI 客户端并将严格 JSON 响应转换为字段结果。"""

    def __init__(
        self,
        client: AITextClient,
        policy: ExtractionPolicy | None = None,
    ) -> None:
        self._client = client
        self._policy = policy or ExtractionPolicy()
        self.last_trace: AIExtractionTrace | None = None
        self.traces: list[AIExtractionTrace] = []

    def extract(self, document: ParsedDocument) -> dict[str, FieldResult]:
        prompt = build_extraction_prompt(document)
        self.last_trace = None
        self.traces = []
        for attempt in range(1, self._policy.max_attempts + 1):
            try:
                response = self._client.complete(
                    prompt.system_prompt,
                    prompt.user_prompt,
                    self._policy.timeout_seconds,
                )
            except AIClientError as error:
                if error.retryable and attempt < self._policy.max_attempts:
                    continue
                reason = "调用超时或暂时不可用" if error.retryable else "调用配置或权限错误"
                raise ManualReviewRequired(reason, attempt) from None

            try:
                return self._validate_response(document, response)
            except FieldExtractionError as error:
                if attempt < self._policy.max_attempts:
                    continue
                raise ManualReviewRequired("响应不符合字段契约", attempt) from None

        raise AssertionError("有限尝试循环不应无结果结束")

    def _validate_response(
        self,
        document: ParsedDocument,
        response: AITextResponse,
    ) -> dict[str, FieldResult]:
        if not isinstance(response, AITextResponse):
            raise FieldExtractionError("AI 客户端必须返回 AITextResponse")
        trace = AIExtractionTrace(
            document_id=document.document_id,
            service=response.service,
            model=response.model,
            parameters=response.parameters,
            request_id=response.request_id,
            prompt_version=PROMPT_VERSION,
            extractor_version=EXTRACTOR_VERSION,
            recorded_at=datetime.now(timezone.utc),
        )
        self.last_trace = trace
        self.traces.append(trace)
        try:
            payload = _load_json_object(response.text)
            results = {
                field_name: _parse_field_result(field_name, value)
                for field_name, value in payload.items()
            }
            validate_extraction_results(document, results)
            return results
        except (FieldExtractionError, ValueError) as error:
            raise FieldExtractionError("AI 响应不符合字段契约") from None


def _load_json_object(response: str) -> dict[str, Any]:
    if not isinstance(response, str):
        raise FieldExtractionError("AI 响应必须是字符串")
    try:
        payload = json.loads(response, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError) as error:
        raise FieldExtractionError("AI 响应不是有效的唯一键 JSON") from error
    if not isinstance(payload, dict):
        raise FieldExtractionError("AI 响应根节点必须是 JSON 对象")
    return payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("存在重复键")
        result[key] = value
    return result


def _parse_field_result(field_name: str, value: Any) -> FieldResult:
    data = _require_object(value, f"字段 {field_name}")
    _require_exact_keys(
        data,
        {"raw_value", "confidence", "evidence", "candidates"},
        f"字段 {field_name}",
    )
    raw_value = _optional_non_empty_string(data["raw_value"], f"字段 {field_name}.raw_value")
    confidence = _optional_confidence(data["confidence"], f"字段 {field_name}.confidence")
    evidence = _parse_list(
        data["evidence"],
        lambda item: _parse_evidence(item, f"字段 {field_name}.evidence"),
        f"字段 {field_name}.evidence",
    )
    candidates = _parse_list(
        data["candidates"],
        lambda item: _parse_candidate(item, field_name),
        f"字段 {field_name}.candidates",
    )
    return FieldResult(
        field_name=field_name,
        raw_value=raw_value,
        confidence=confidence,
        evidence=evidence,
        candidates=candidates,
    )


def _parse_candidate(value: Any, field_name: str) -> FieldCandidate:
    context = f"字段 {field_name}.candidates 项"
    data = _require_object(value, context)
    _require_exact_keys(data, {"value", "confidence", "evidence"}, context)
    candidate_value = _required_non_empty_string(data["value"], f"{context}.value")
    confidence = _optional_confidence(data["confidence"], f"{context}.confidence")
    evidence = tuple(
        _parse_list(
            data["evidence"],
            lambda item: _parse_evidence(item, f"{context}.evidence"),
            f"{context}.evidence",
        )
    )
    return FieldCandidate(candidate_value, confidence, evidence)


def _parse_evidence(value: Any, context: str) -> SourceEvidence:
    data = _require_object(value, context)
    _require_exact_keys(
        data,
        {"document_id", "page_number", "text_excerpt"},
        context,
    )
    document_id = _required_non_empty_string(data["document_id"], f"{context}.document_id")
    page_number = data["page_number"]
    if isinstance(page_number, bool) or not isinstance(page_number, int):
        raise FieldExtractionError(f"{context}.page_number 必须是整数")
    text_excerpt = _required_non_empty_string(
        data["text_excerpt"], f"{context}.text_excerpt"
    )
    return SourceEvidence(document_id, page_number, text_excerpt)


def _require_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FieldExtractionError(f"{context} 必须是 JSON 对象")
    if not all(isinstance(key, str) for key in value):
        raise FieldExtractionError(f"{context} 的键必须是字符串")
    return value


def _require_exact_keys(data: dict[str, Any], expected: set[str], context: str) -> None:
    actual = set(data)
    if actual != expected:
        missing = ", ".join(sorted(expected - actual)) or "无"
        unexpected = ", ".join(sorted(actual - expected)) or "无"
        raise FieldExtractionError(
            f"{context} 属性不符合 Schema；缺少: {missing}；多余: {unexpected}"
        )


def _parse_list(
    value: Any,
    parser: Callable[[Any], Any],
    context: str,
) -> list[Any]:
    if not isinstance(value, list):
        raise FieldExtractionError(f"{context} 必须是数组")
    return [parser(item) for item in value]


def _optional_non_empty_string(value: Any, context: str) -> str | None:
    if value is None:
        return None
    return _required_non_empty_string(value, context)


def _required_non_empty_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FieldExtractionError(f"{context} 必须是非空字符串")
    return value


def _optional_confidence(value: Any, context: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FieldExtractionError(f"{context} 必须是 0 到 1 的数字或 null")
    if not 0 <= value <= 1:
        raise FieldExtractionError(f"{context} 必须在 0 到 1 之间")
    return float(value)
