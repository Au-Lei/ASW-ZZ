"""测试使用的可控替身。"""

from __future__ import annotations

from copy import deepcopy

from app.state import FieldResult
from app.tools.ai_field_extractor import AIClientError, AITextResponse
from app.tools.document_parser import ParsedDocument


class FakeFieldExtractor:
    """按文档 ID 返回预设结果，不从文本推断或补写任何字段。"""

    def __init__(
        self,
        field_names: tuple[str, ...],
        responses: dict[str, dict[str, FieldResult]] | None = None,
    ) -> None:
        self._field_names = field_names
        self._responses = responses or {}

    def extract(self, document: ParsedDocument) -> dict[str, FieldResult]:
        configured = self._responses.get(document.document_id, {})
        unknown_fields = set(configured) - set(self._field_names)
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ValueError(f"存在未声明字段: {names}")

        results: dict[str, FieldResult] = {}
        for field_name in self._field_names:
            result = configured.get(field_name, FieldResult(field_name=field_name))
            if result.field_name != field_name:
                raise ValueError(f"字段键与 FieldResult.field_name 不一致: {field_name}")
            results[field_name] = deepcopy(result)
        return results


class FakeAITextClient:
    """返回预设文本并记录最后一次调用的 AI 客户端替身。"""

    def __init__(
        self,
        response: str,
        *,
        service: str = "fake-ai",
        model: str = "fake-model-v1",
        parameters: tuple[tuple[str, str | int | float | bool | None], ...] = (),
        request_id: str | None = "fake-request-001",
    ) -> None:
        self.response = AITextResponse(
            text=response,
            service=service,
            model=model,
            parameters=parameters,
            request_id=request_id,
        )
        self.system_prompt: str | None = None
        self.user_prompt: str | None = None
        self.timeout_seconds: float | None = None
        self.call_count = 0

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float,
    ) -> AITextResponse:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.timeout_seconds = timeout_seconds
        self.call_count += 1
        return self.response


class SequencedFakeAITextClient:
    """按顺序返回响应或抛出已分类错误的测试客户端。"""

    def __init__(self, outcomes: list[AITextResponse | AIClientError]) -> None:
        self._outcomes = outcomes
        self.call_count = 0
        self.timeouts: list[float] = []

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float,
    ) -> AITextResponse:
        self.timeouts.append(timeout_seconds)
        outcome = self._outcomes[self.call_count]
        self.call_count += 1
        if isinstance(outcome, AIClientError):
            raise outcome
        return outcome
