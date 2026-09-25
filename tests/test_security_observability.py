"""密钥、错误信息和任务指标的安全边界测试。"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.metrics import summarize_task_metrics
from app.secrets import SecretConfigurationError, read_secret
from app.state import (
    DocumentTask, FieldReviewAudit, HumanReviewStatus, ReviewAction,
    TaskStatus, TaskStatusAudit,
)
from app.tools.ai_field_extractor import AITextResponse
from app.tools.field_extractor import FieldExtractionError
from app.tools.ai_field_extractor import _load_json_object


class SecretAndLeakageTests(unittest.TestCase):
    def test_secret_is_only_read_from_environment_and_masked_in_repr(self) -> None:
        value = read_secret("ASW_AI_API_KEY", {"ASW_AI_API_KEY": "sensitive-value"})
        self.assertEqual(value.reveal(), "sensitive-value")
        self.assertNotIn("sensitive-value", repr(value))
        with self.assertRaises(SecretConfigurationError):
            read_secret("ASW_AI_API_KEY", {})
        with self.assertRaises(SecretConfigurationError):
            read_secret("UNRECOGNIZED_SECRET", {"UNRECOGNIZED_SECRET": "x"})

    def test_ai_trace_rejects_authentication_parameters(self) -> None:
        for name in ("api_key", "access_token", "Authorization", "client_secret"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                AITextResponse("{}", "ai", "model", ((name, "sensitive-value"),))

    def test_invalid_json_and_duplicate_key_errors_do_not_echo_input(self) -> None:
        payloads = ('{"customer-secret":', '{"customer-secret": 1, "customer-secret": 2}')
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(FieldExtractionError) as raised:
                _load_json_object(payload)
            self.assertNotIn("customer-secret", str(raised.exception))

    def test_untrusted_schema_keys_are_not_exposed_in_extractor_error(self) -> None:
        from app.tools.ai_field_extractor import SchemaValidatingFieldExtractor
        from app.tools.document_parser import ParsedDocument, ParsedPage

        class Client:
            def complete(self, system_prompt, user_prompt, timeout_seconds):
                return AITextResponse('{"customer-secret": {}}', "ai", "model")

        extractor = SchemaValidatingFieldExtractor(Client())
        document = ParsedDocument("doc-1", (ParsedPage(1, "text"),))
        with self.assertRaises(FieldExtractionError) as raised:
            extractor.extract(document)
        self.assertNotIn("customer-secret", str(raised.exception))

    def test_fixture_and_example_contain_no_private_secret_marker(self) -> None:
        root = Path(__file__).resolve().parents[1]
        fixture = (root / "tests" / "fixtures" / "maersk_001_expected.json").read_text(encoding="utf-8")
        example = (root / ".env.example").read_text(encoding="utf-8")
        json.loads(fixture)
        for line in example.splitlines():
            if line.startswith("ASW_"):
                self.assertEqual(line.split("=", 1)[1], "")
        self.assertNotIn("BEGIN PRIVATE KEY", fixture + example)


class MetricsTests(unittest.TestCase):
    def test_summarizes_durations_safe_error_codes_and_latest_review_action(self) -> None:
        start = datetime(2026, 9, 26, tzinfo=timezone.utc)
        task = DocumentTask("task-1", status=TaskStatus.FAILED)
        task.status_history = [
            TaskStatusAudit(TaskStatus.UPLOADED, TaskStatus.PARSING, start),
            TaskStatusAudit(TaskStatus.PARSING, TaskStatus.EXTRACTING, start + timedelta(seconds=3)),
            TaskStatusAudit(TaskStatus.EXTRACTING, TaskStatus.FAILED, start + timedelta(seconds=8), "timeout"),
        ]
        task.updated_at = start + timedelta(seconds=8)
        task.parser_version = "parser-v1"
        task.review_history = [
            FieldReviewAudit("carrier", ReviewAction.MODIFIED, HumanReviewStatus.UNREVIEWED,
                HumanReviewStatus.MODIFIED, None, "private carrier", "operator", start),
            FieldReviewAudit("carrier", ReviewAction.ACCEPTED, HumanReviewStatus.MODIFIED,
                HumanReviewStatus.ACCEPTED, "private carrier", "private carrier", "operator", start),
            FieldReviewAudit("voyage", ReviewAction.MODIFIED, HumanReviewStatus.UNREVIEWED,
                HumanReviewStatus.MODIFIED, None, "private voyage", "operator", start),
        ]
        metrics = summarize_task_metrics(task)
        self.assertEqual([duration.seconds for duration in metrics.stage_durations], [3, 5])
        self.assertEqual(metrics.error_codes, ("timeout",))
        self.assertEqual(metrics.modification_rate, 0.5)
        self.assertEqual(metrics.parser_version, "parser-v1")
        self.assertNotIn("private carrier", repr(metrics))
        self.assertNotIn("private voyage", repr(metrics))

    def test_no_review_has_unknown_rate(self) -> None:
        metrics = summarize_task_metrics(DocumentTask("task-2"))
        self.assertIsNone(metrics.modification_rate)
        self.assertEqual(metrics.stage_durations, ())


if __name__ == "__main__":
    unittest.main()
