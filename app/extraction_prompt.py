"""基于版本化模板构建字段提取提示词。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.extraction_schema import CORE_FIELD_DEFINITIONS
from app.tools.document_parser import ParsedDocument


PROMPT_VERSION = "field-extraction-v1"
_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "field_extraction_v1.txt"
)


@dataclass(frozen=True, slots=True)
class ExtractionPrompt:
    """一次可追踪的系统提示词和用户提示词组合。"""

    version: str
    system_prompt: str
    user_prompt: str


def build_extraction_prompt(document: ParsedDocument) -> ExtractionPrompt:
    """使用稳定字段定义与页级原文生成提示词。"""

    field_lines = "\n".join(
        f'- "{item.name}"（{item.label}）：{item.description}'
        for item in CORE_FIELD_DEFINITIONS
    )
    page_sections = "\n\n".join(
        f"<page number=\"{page.page_number}\">\n{page.text}\n</page>"
        for page in document.pages
    )
    user_prompt = f"""document_id: {document.document_id}

请提取以下全部字段，字段不得缺失或增加：
{field_lines}

输出一个以字段内部名为键的 JSON 根对象。每个字段使用以下结构：
{{
  "raw_value": string | null,
  "confidence": number | null,
  "evidence": [{{"document_id": string, "page_number": integer, "text_excerpt": string}}],
  "candidates": [{{"value": string, "confidence": number | null, "evidence": [...]}}]
}}

待提取文档开始：
<document>
{page_sections}
</document>
待提取文档结束。"""

    return ExtractionPrompt(
        version=PROMPT_VERSION,
        system_prompt=_TEMPLATE_PATH.read_text(encoding="utf-8").strip(),
        user_prompt=user_prompt,
    )
