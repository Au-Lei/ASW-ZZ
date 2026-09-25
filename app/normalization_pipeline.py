"""将已实现的确定性规则串联为统一标准化管线。"""

from __future__ import annotations

from dataclasses import dataclass

from app.alias_mapping import (
    EntityType,
    MappingRegistry,
    MappingStatus,
    apply_alias_mapping,
    mapping_issue,
)
from app.container_normalization import ContainerItem, normalize_container_summary
from app.date_normalization import normalize_etd
from app.field_validation import FieldValidationPolicy, assess_field_results
from app.identifier_normalization import normalize_identifiers
from app.state import FieldResult
from app.vessel_voyage_normalization import normalize_vessel_and_voyage


RULESET_VERSION = "normalization-v1"
_MAPPED_FIELDS = {
    "carrier": EntityType.CARRIER,
    "yard": EntityType.YARD,
    "port_of_loading": EntityType.PORT,
    "port_of_discharge": EntityType.PORT,
}


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    field_results: dict[str, FieldResult]
    container_items: tuple[ContainerItem, ...]
    ruleset_version: str
    mapping_version: str


class NormalizationPipeline:
    def __init__(
        self,
        registry: MappingRegistry,
        validation_policy: FieldValidationPolicy,
    ) -> None:
        self._registry = registry
        self._validation_policy = validation_policy

    def normalize(self, results: dict[str, FieldResult]) -> NormalizationResult:
        normalized = assess_field_results(results, self._validation_policy)
        normalized = normalize_identifiers(normalized)
        normalized = normalize_vessel_and_voyage(normalized)

        if "etd" in normalized:
            normalized["etd"] = normalize_etd(normalized["etd"])

        container_items: tuple[ContainerItem, ...] = ()
        if "container_summary" in normalized:
            container_result = normalize_container_summary(
                normalized["container_summary"]
            )
            normalized["container_summary"] = container_result.field_result
            container_items = self._map_container_types(
                normalized["container_summary"], container_result.items
            )

        for field_name, entity_type in _MAPPED_FIELDS.items():
            if field_name in normalized:
                normalized[field_name] = apply_alias_mapping(
                    normalized[field_name], entity_type, self._registry
                )

        return NormalizationResult(
            field_results=normalized,
            container_items=container_items,
            ruleset_version=RULESET_VERSION,
            mapping_version=self._registry.version,
        )

    def _map_container_types(
        self,
        result: FieldResult,
        items: tuple[ContainerItem, ...],
    ) -> tuple[ContainerItem, ...]:
        container_mapping_issues = {
            mapping_issue(EntityType.CONTAINER_TYPE, MappingStatus.UNMAPPED),
            mapping_issue(EntityType.CONTAINER_TYPE, MappingStatus.AMBIGUOUS),
        }
        result.validation_issues = [
            issue
            for issue in result.validation_issues
            if issue not in container_mapping_issues
        ]
        mapped_items: list[ContainerItem] = []
        all_matched = bool(items)
        for item in items:
            decision = self._registry.resolve(
                EntityType.CONTAINER_TYPE, item.type_code
            )
            if decision.status != MappingStatus.MATCHED:
                all_matched = False
                issue = mapping_issue(EntityType.CONTAINER_TYPE, decision.status)
                if issue not in result.validation_issues:
                    result.validation_issues.append(issue)
                mapped_items.append(item)
                continue
            assert decision.normalized_value is not None
            mapped_items.append(
                ContainerItem(item.quantity, item.size, decision.normalized_value)
            )

        if all_matched:
            result.normalized_value = " + ".join(
                f"{item.quantity}X{item.size}{item.type_code}"
                for item in mapped_items
            )
        return tuple(mapped_items)
