"""人工复核页面的数据契约与安全 HTML 展示。"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Mapping

from app.extraction_schema import CORE_FIELD_NAMES
from app.state import DocumentTask, SourceEvidence, TaskStatus
from app.tools.document_parser import ParsedDocument


@dataclass(frozen=True, slots=True)
class EvidenceView:
    page_number: int | None
    excerpt: str | None
    region: tuple[float, float, float, float] | None


@dataclass(frozen=True, slots=True)
class CandidateView:
    value: str
    confidence: float | None
    evidence: tuple[EvidenceView, ...]


@dataclass(frozen=True, slots=True)
class FieldReviewView:
    name: str
    raw_value: str | None
    normalized_value: str | None
    final_value: str | None
    confidence: float | None
    candidates: tuple[CandidateView, ...]
    issues: tuple[str, ...]
    evidence: tuple[EvidenceView, ...]
    review_status: str


@dataclass(frozen=True, slots=True)
class PageReviewView:
    number: int
    text: str
    image_url: str | None


@dataclass(frozen=True, slots=True)
class ReviewView:
    task_id: str
    filename: str
    document_id: str
    pages: tuple[PageReviewView, ...]
    fields: tuple[FieldReviewView, ...]


def build_review_view(
    task: DocumentTask,
    document: ParsedDocument,
    page_images: Mapping[int, str] | None = None,
) -> ReviewView:
    """把字段证据绑定到原文页；图像 URL 由受控访问层提供。"""

    if task.status not in (TaskStatus.PENDING_REVIEW, TaskStatus.CONFIRMED):
        raise ValueError("只有待复核或已确认任务可以展示复核视图")
    sources = {source.document_id: source for source in task.source_documents}
    if document.document_id not in sources:
        raise ValueError("解析文档不属于任务")
    images = page_images or {}
    page_numbers = {page.page_number for page in document.pages}
    if not set(images) <= page_numbers:
        raise ValueError("页图引用包含不存在的页码")
    for url in images.values():
        if not url.startswith("/review-assets/") or any(char in url for char in ('"', "'", "<", ">", "?", "#")):
            raise ValueError("页图必须使用受控的站内地址")

    def evidence_view(item: SourceEvidence) -> EvidenceView:
        if item.document_id != document.document_id:
            raise ValueError("字段证据指向其他文档")
        page = next((page for page in document.pages if page.page_number == item.page_number), None)
        if item.page_number is not None and page is None:
            raise ValueError("字段证据页码不存在")
        region = None
        if page is not None and item.text_excerpt:
            match = next((part for part in page.regions if item.text_excerpt in part.text), None)
            if match is not None:
                region = (match.left, match.top, match.right, match.bottom)
        return EvidenceView(item.page_number, item.text_excerpt, region)

    fields = []
    for name in CORE_FIELD_NAMES:
        result = task.field_results.get(name)
        if result is None:
            continue
        fields.append(FieldReviewView(
            name=name,
            raw_value=result.raw_value,
            normalized_value=result.normalized_value,
            final_value=result.final_value,
            confidence=result.confidence,
            candidates=tuple(CandidateView(candidate.value, candidate.confidence,
                tuple(evidence_view(item) for item in candidate.evidence)) for candidate in result.candidates),
            issues=tuple(result.validation_issues),
            evidence=tuple(evidence_view(item) for item in result.evidence),
            review_status=result.review_status.value,
        ))
    return ReviewView(
        task.task_id,
        sources[document.document_id].filename,
        document.document_id,
        tuple(PageReviewView(page.page_number, page.text, images.get(page.page_number)) for page in document.pages),
        tuple(fields),
    )


def render_review_html(view: ReviewView) -> str:
    """生成无脚本、转义所有业务文本的证据对照页面。"""

    def value(text: str | None) -> str:
        return escape(text) if text is not None else "—"

    page_html = []
    for page in view.pages:
        image = f'<img src="{escape(page.image_url, quote=True)}" alt="第 {page.number} 页图像">' if page.image_url else ""
        page_html.append(f'<section id="page-{page.number}"><h2>第 {page.number} 页</h2>{image}<pre>{value(page.text)}</pre></section>')

    field_html = []
    for field in view.fields:
        def evidence_html(item: EvidenceView) -> str:
            location = (
                f'<a href="#page-{item.page_number}">第 {item.page_number} 页</a>'
                if item.page_number is not None else "页码未知"
            )
            coordinates = (
                f'（区域 {", ".join(str(number) for number in item.region)}）'
                if item.region is not None else ""
            )
            return f'<li>{location}：{value(item.excerpt)}{coordinates}</li>'

        evidence = "".join(evidence_html(item) for item in field.evidence)
        candidates = "".join(
            f'<li>{value(item.value)}<ul>{"".join(evidence_html(source) for source in item.evidence)}</ul></li>'
            for item in field.candidates
        ) or "<li>—</li>"
        issues = "；".join(value(item) for item in field.issues) or "—"
        confidence = f"{field.confidence:.2f}" if field.confidence is not None else "—"
        field_html.append(
            f'<section><h2>{escape(field.name)}</h2><dl>'
            f'<dt>原始值</dt><dd>{value(field.raw_value)}</dd>'
            f'<dt>标准化建议</dt><dd>{value(field.normalized_value)}</dd>'
            f'<dt>最终值</dt><dd>{value(field.final_value)}</dd>'
            f'<dt>置信度</dt><dd>{confidence}</dd>'
            f'<dt>候选</dt><dd><ul>{candidates}</ul></dd>'
            f'<dt>异常原因</dt><dd>{issues}</dd>'
            f'<dt>复核状态</dt><dd>{escape(field.review_status)}</dd>'
            f'</dl><ul>{evidence}</ul></section>'
        )
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<title>字段复核</title></head><body><h1>文件：'
            f'{escape(view.filename)}</h1><main><div>{"".join(page_html)}</div>'
            f'<div>{"".join(field_html)}</div></main></body></html>')
