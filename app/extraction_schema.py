"""第一阶段字段提取 Schema 及确定性契约校验。"""

from __future__ import annotations

from dataclasses import dataclass

from app.state import FieldResult, SourceEvidence
from app.tools.document_parser import ParsedDocument
from app.tools.field_extractor import FieldExtractionError


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    """一个稳定内部字段的业务含义。"""

    name: str
    label: str
    description: str


CORE_FIELD_DEFINITIONS = (
    FieldDefinition("carrier", "承运公司", "入货通知中明确标注的承运公司"),
    FieldDefinition("salesperson", "揽货人", "内部揽货人；本阶段允许为空"),
    FieldDefinition("customer_service", "客服", "内部客服；本阶段允许为空"),
    FieldDefinition("booking_no", "提单号/Booking No.", "通知中明确标注的订舱编号"),
    FieldDefinition("contract_no", "合约号", "通知中明确标注的合约编号"),
    FieldDefinition("vessel_name", "船名", "通知中明确标注的船舶名称"),
    FieldDefinition("voyage", "航次", "通知中明确标注的航次"),
    FieldDefinition("etd", "船期/ETD", "通知中的预计离港日期；不得猜测缺失年份"),
    FieldDefinition("container_summary", "箱量及类型", "通知中明确标注的箱量与箱型原文"),
    FieldDefinition("yard", "场站", "通知中明确标注的进箱场站"),
    FieldDefinition("port_of_loading", "起运港", "通知中明确标注的起运港"),
    FieldDefinition("port_of_discharge", "目的港", "通知中明确标注的目的港"),
)

CORE_FIELD_NAMES = tuple(item.name for item in CORE_FIELD_DEFINITIONS)


def validate_extraction_results(
    document: ParsedDocument,
    results: dict[str, FieldResult],
) -> None:
    """严格校验提取结果的字段集合、字段名和来源证据。"""

    expected = set(CORE_FIELD_NAMES)
    actual = set(results)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"缺少字段: {', '.join(missing)}")
        if unexpected:
            details.append(f"未知字段: {', '.join(unexpected)}")
        raise FieldExtractionError("；".join(details))

    for field_name, result in results.items():
        if result.field_name != field_name:
            raise FieldExtractionError(
                f"字段键与 FieldResult.field_name 不一致: {field_name}"
            )
        if result.raw_value is not None and not result.evidence:
            raise FieldExtractionError(f"字段 {field_name} 有值但缺少来源证据")
        _validate_evidence(document, field_name, result.evidence)

        for candidate in result.candidates:
            if not candidate.evidence:
                raise FieldExtractionError(
                    f"字段 {field_name} 的候选值 {candidate.value} 缺少来源证据"
                )
            _validate_evidence(document, field_name, candidate.evidence)


def _validate_evidence(
    document: ParsedDocument,
    field_name: str,
    evidence_items: list[SourceEvidence] | tuple[SourceEvidence, ...],
) -> None:
    pages = {page.page_number: page.text for page in document.pages}
    for evidence in evidence_items:
        if evidence.document_id != document.document_id:
            raise FieldExtractionError(f"字段 {field_name} 的证据指向其他文档")
        if evidence.page_number is not None and evidence.page_number not in pages:
            raise FieldExtractionError(f"字段 {field_name} 的证据页码不存在")
        if (
            evidence.page_number is not None
            and evidence.text_excerpt is not None
            and evidence.text_excerpt not in pages[evidence.page_number]
        ):
            raise FieldExtractionError(f"字段 {field_name} 的证据原文不在指定页面")
