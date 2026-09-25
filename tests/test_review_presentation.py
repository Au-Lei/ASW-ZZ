"""复核证据对照与安全展示测试。"""

import unittest

from app.review_presentation import build_review_view, render_review_html
from app.state import DocumentTask, FieldCandidate, FieldResult, SourceDocument, SourceEvidence, TaskStatus
from app.tools.document_parser import ParsedDocument, ParsedPage, TextRegion


def _task() -> DocumentTask:
    evidence = SourceEvidence("doc-1", 2, "Booking 123")
    return DocumentTask(
        "task-1", status=TaskStatus.PENDING_REVIEW,
        source_documents=[SourceDocument("doc-1", "notice.pdf")],
        field_results={"booking_no": FieldResult(
            "booking_no", raw_value="Booking 123", normalized_value="123",
            confidence=0.8, evidence=[evidence],
            candidates=[FieldCandidate("123", 0.8, (evidence,))],
            validation_issues=["需要确认"],
        )},
    )


def _document() -> ParsedDocument:
    return ParsedDocument("doc-1", (
        ParsedPage(1, "cover"),
        ParsedPage(2, "Booking 123", (TextRegion("Booking 123", 1, 2, 30, 10),)),
    ))


class ReviewPresentationTests(unittest.TestCase):
    def test_links_field_and_candidate_evidence_to_page_and_region(self) -> None:
        view = build_review_view(_task(), _document(), {2: "/review-assets/doc-1/page-2"})
        field = view.fields[0]
        self.assertEqual(field.evidence[0].page_number, 2)
        self.assertEqual(field.evidence[0].region, (1, 2, 30, 10))
        self.assertEqual(field.candidates[0].evidence[0].page_number, 2)
        html = render_review_html(view)
        self.assertIn('href="#page-2"', html)
        self.assertIn('src="/review-assets/doc-1/page-2"', html)
        self.assertIn("区域 1, 2, 30, 10", html)
        for label in ("原始值", "标准化建议", "置信度", "候选", "异常原因"):
            self.assertIn(label, html)

    def test_escapes_untrusted_document_field_and_filename(self) -> None:
        task = _task()
        task.source_documents = [SourceDocument("doc-1", '<img src=x onerror=alert(1)>.pdf')]
        task.field_results["booking_no"].validation_issues = ["<script>alert(1)</script>"]
        document = ParsedDocument("doc-1", (ParsedPage(1, "<script>bad</script>"),))
        task.field_results["booking_no"].evidence = []
        task.field_results["booking_no"].candidates = []
        html = render_review_html(build_review_view(task, document))
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)

    def test_rejects_wrong_document_evidence_and_unsafe_image_url(self) -> None:
        task = _task()
        with self.assertRaises(ValueError):
            build_review_view(task, _document(), {2: "javascript:alert(1)"})
        task.field_results["booking_no"].evidence = [SourceEvidence("other", 2, "Booking 123")]
        with self.assertRaises(ValueError):
            build_review_view(task, _document())


if __name__ == "__main__":
    unittest.main()
