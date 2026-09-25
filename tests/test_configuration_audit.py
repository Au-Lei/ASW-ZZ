"""映射及规则变更审计测试。"""

import unittest
from datetime import datetime, timezone

from app.alias_mapping import AliasMapping, EntityType, MappingRegistry
from app.configuration_audit import ConfigurationKind, ConfigurationLedger, audit_mapping_change, audit_ruleset_change


NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


class ConfigurationAuditTests(unittest.TestCase):
    def test_ledger_publishes_config_and_history_together(self) -> None:
        original = ConfigurationLedger(MappingRegistry("map-v1", ()), "rules-v1")
        mapped = original.replace_mappings(
            MappingRegistry("map-v2", (AliasMapping(EntityType.PORT, "QD", "青岛"),)),
            "operator-1", NOW, "加入港口别名",
        )
        published = mapped.publish_ruleset("rules-v2", "operator-1", NOW, "更新日期规则")
        self.assertEqual(original.history, ())
        self.assertEqual(published.mapping_registry.version, "map-v2")
        self.assertEqual(published.ruleset_version, "rules-v2")
        self.assertEqual(tuple(item.kind for item in published.history),
                         (ConfigurationKind.MAPPING, ConfigurationKind.RULESET))

    def test_records_mapping_before_after_version_and_changed_entries(self) -> None:
        old = AliasMapping(EntityType.PORT, "QD", "青岛", enabled=False)
        new = AliasMapping(EntityType.PORT, "QD", "青岛", enabled=True)
        audit = audit_mapping_change(
            MappingRegistry("v1", (old,)), MappingRegistry("v2", (new,)),
            "operator-1", NOW, "业务确认启用",
        )
        self.assertEqual(audit.kind, ConfigurationKind.MAPPING)
        self.assertEqual(audit.added_entries, (new,))
        self.assertEqual(audit.removed_entries, (old,))
        self.assertEqual(audit.operator, "operator-1")

    def test_records_ruleset_release_with_reason(self) -> None:
        audit = audit_ruleset_change("rules-v1", "rules-v2", "operator-1", NOW, "修正日期规则")
        self.assertEqual(audit.kind, ConfigurationKind.RULESET)
        self.assertEqual(audit.new_version, "rules-v2")

    def test_rejects_noop_unversioned_and_unaudited_changes(self) -> None:
        entry = AliasMapping(EntityType.YARD, "A", "场站 A")
        registry = MappingRegistry("v1", (entry,))
        with self.assertRaises(ValueError):
            audit_mapping_change(registry, MappingRegistry("v2", (entry,)), "operator", NOW, "reason")
        with self.assertRaises(ValueError):
            audit_ruleset_change("v1", "v1", "operator", NOW, "reason")
        with self.assertRaises(ValueError):
            audit_ruleset_change("v1", "v2", "", NOW, "reason")
        with self.assertRaises(ValueError):
            audit_ruleset_change("v1", "v2", "operator", datetime(2026, 9, 26), "reason")


if __name__ == "__main__":
    unittest.main()
