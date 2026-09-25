"""通用版本化别名映射测试。"""

import unittest

from app.alias_mapping import (
    AliasMapping,
    EntityType,
    MappingRegistry,
    MappingStatus,
    apply_alias_mapping,
    mapping_issue,
)
from app.state import FieldResult


class MappingRegistryTests(unittest.TestCase):
    def test_resolves_single_enabled_mapping_case_and_space_insensitively(self) -> None:
        registry = MappingRegistry(
            "mapping-test-v1",
            (AliasMapping(EntityType.CARRIER, "Maersk  Line", "马士基"),),
        )

        decision = registry.resolve(EntityType.CARRIER, "  MAERSK line ")

        self.assertEqual(decision.status, MappingStatus.MATCHED)
        self.assertEqual(decision.normalized_value, "马士基")

    def test_ignores_disabled_mapping(self) -> None:
        registry = MappingRegistry(
            "mapping-test-v1",
            (AliasMapping(EntityType.PORT, "QD", "青岛", enabled=False),),
        )

        decision = registry.resolve(EntityType.PORT, "QD")

        self.assertEqual(decision.status, MappingStatus.UNMAPPED)

    def test_multiple_enabled_entries_are_ambiguous_even_if_values_match(self) -> None:
        registry = MappingRegistry(
            "mapping-test-v1",
            (
                AliasMapping(EntityType.PORT, "QD", "青岛"),
                AliasMapping(EntityType.PORT, "qd", "青岛"),
            ),
        )

        decision = registry.resolve(EntityType.PORT, "QD")

        self.assertEqual(decision.status, MappingStatus.AMBIGUOUS)
        self.assertIsNone(decision.normalized_value)

    def test_requires_mapping_version_and_non_empty_values(self) -> None:
        with self.assertRaises(ValueError):
            MappingRegistry("", ())
        with self.assertRaises(ValueError):
            AliasMapping(EntityType.YARD, "", "标准场站")
        with self.assertRaises(ValueError):
            AliasMapping(EntityType.YARD, "场站别名", "")


class ApplyAliasMappingTests(unittest.TestCase):
    def test_only_unique_match_sets_normalized_value(self) -> None:
        registry = MappingRegistry(
            "mapping-test-v1",
            (AliasMapping(EntityType.YARD, "港联海", "青岛港联海"),),
        )

        result = apply_alias_mapping(
            FieldResult("yard", raw_value="港联海"),
            EntityType.YARD,
            registry,
        )

        self.assertEqual(result.normalized_value, "青岛港联海")
        self.assertEqual(result.validation_issues, [])

    def test_unmapped_and_ambiguous_values_remain_unstandardized(self) -> None:
        registries = (
            (
                MappingRegistry("v1", ()),
                MappingStatus.UNMAPPED,
            ),
            (
                MappingRegistry(
                    "v2",
                    (
                        AliasMapping(EntityType.PORT, "MANILA", "PHMNL"),
                        AliasMapping(EntityType.PORT, "Manila", "马尼拉"),
                    ),
                ),
                MappingStatus.AMBIGUOUS,
            ),
        )
        for registry, status in registries:
            with self.subTest(status=status):
                result = apply_alias_mapping(
                    FieldResult("port_of_discharge", raw_value="Manila"),
                    EntityType.PORT,
                    registry,
                )
                self.assertIsNone(result.normalized_value)
                self.assertEqual(
                    result.validation_issues,
                    [mapping_issue(EntityType.PORT, status)],
                )


if __name__ == "__main__":
    unittest.main()
