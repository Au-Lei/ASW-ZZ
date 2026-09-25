"""版本化映射与规则配置的变更审计。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.alias_mapping import AliasMapping, MappingRegistry


class ConfigurationKind(StrEnum):
    MAPPING = "mapping"
    RULESET = "ruleset"


@dataclass(frozen=True, slots=True)
class ConfigurationChangeAudit:
    kind: ConfigurationKind
    previous_version: str
    new_version: str
    operator: str
    changed_at: datetime
    reason: str
    added_entries: tuple[AliasMapping, ...] = ()
    removed_entries: tuple[AliasMapping, ...] = ()

    def __post_init__(self) -> None:
        if not self.previous_version.strip() or not self.new_version.strip():
            raise ValueError("配置版本不能为空")
        if self.previous_version == self.new_version:
            raise ValueError("配置变更必须产生新版本")
        if not self.operator.strip() or not self.reason.strip():
            raise ValueError("配置变更必须记录操作人和原因")
        if self.changed_at.tzinfo is None:
            raise ValueError("变更时间必须包含时区")


@dataclass(frozen=True, slots=True)
class ConfigurationLedger:
    """配置快照与审计历史一同发布。"""

    mapping_registry: MappingRegistry
    ruleset_version: str
    history: tuple[ConfigurationChangeAudit, ...] = ()

    def __post_init__(self) -> None:
        if not self.ruleset_version.strip():
            raise ValueError("规则版本不能为空")

    def replace_mappings(
        self, updated: MappingRegistry, operator: str, changed_at: datetime, reason: str
    ) -> ConfigurationLedger:
        audit = audit_mapping_change(
            self.mapping_registry, updated, operator, changed_at, reason
        )
        return ConfigurationLedger(updated, self.ruleset_version, self.history + (audit,))

    def publish_ruleset(
        self, version: str, operator: str, changed_at: datetime, reason: str
    ) -> ConfigurationLedger:
        audit = audit_ruleset_change(
            self.ruleset_version, version, operator, changed_at, reason
        )
        return ConfigurationLedger(self.mapping_registry, version, self.history + (audit,))


def audit_mapping_change(
    previous: MappingRegistry,
    updated: MappingRegistry,
    operator: str,
    changed_at: datetime,
    reason: str,
) -> ConfigurationChangeAudit:
    """记录映射增删与版本；条目变更呈现为删除旧值、增加新值。"""

    if previous.entries == updated.entries:
        raise ValueError("映射条目未变化")
    added = tuple(item for item in updated.entries if item not in previous.entries)
    removed = tuple(item for item in previous.entries if item not in updated.entries)
    return ConfigurationChangeAudit(
        ConfigurationKind.MAPPING, previous.version, updated.version,
        operator, changed_at, reason, added, removed,
    )


def audit_ruleset_change(
    previous_version: str,
    new_version: str,
    operator: str,
    changed_at: datetime,
    reason: str,
) -> ConfigurationChangeAudit:
    """记录规则版本发布；实际规则内容由版本控制保存。"""

    return ConfigurationChangeAudit(
        ConfigurationKind.RULESET, previous_version, new_version,
        operator, changed_at, reason,
    )
