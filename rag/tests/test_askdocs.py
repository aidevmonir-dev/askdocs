import io
import json
from types import SimpleNamespace

from django.core.cache import cache
from django.test import TestCase, override_settings
from pypdf import PdfWriter

from rag.models import Chunk, Document, Workspace
from rag.services import ingest
from rag.services.answer import answer_question, parse_response
from rag.services.chunking import MAX_CHARS, chunk_pages
from rag.services.extract import ExtractionError, extract_pages
from rag.services.retrieval import Hit, search, tokenize

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def text_block(text, citations=None):
    return SimpleNamespace(type="text", text=text, citations=citations)


def citation(index, quote):
    return SimpleNamespace(type="char_location", document_index=index, cited_text=quote)


class FakeClient:
    """Stands in for anthropic.Anthropic so tests never call the network."""

    def __init__(self, content, stop_reason="end_turn"):
        self.kwargs = None
        self._response = SimpleNamespace(content=content, stop_reason=stop_reason)
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return self._response


class ChunkingTests(TestCase):
    def test_passages_never_cross_pages_and_respect_size(self):
        pages = ["Alpha sentence. " * 200, "Beta paragraph.\n\nGamma paragraph."]
        passages = chunk_pages(pages)
        self.assertTrue(all(len(p.text) <= MAX_CHARS for p in passages))
        self.assertEqual({p.page for p in passages}, {1, 2})
        self.assertTrue(all("Alpha" not in p.text for p in passages if p.page == 2))

    def test_no_text_is_lost(self):
        page = "First paragraph here.\n\nSecond paragraph here.\n\nThird one."
        joined = " ".join(p.text for p in chunk_pages([page]))
        for word in ("First", "Second", "Third"):
            self.assertIn(word, joined)


class ExtractionTests(TestCase):
    def test_rejects_unknown_type_and_fake_pdf(self):
        with self.assertRaises(ExtractionError):
            extract_pages("notes.docx", b"hello", 10)
        with self.assertRaises(ExtractionError):
            extract_pages("fake.pdf", b"not a pdf", 10)

    def test_pdf_without_text_is_explained(self):
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)
        with self.assertRaisesMessage(ExtractionError, "No text was found"):
            extract_pages("blank.pdf", buffer.getvalue(), 10)

    def test_page_limit(self):
        writer = PdfWriter()
        for _ in range(3):
            writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)
        with self.assertRaisesMessage(ExtractionError, "The limit is 2"):
            extract_pages("long.pdf", buffer.getvalue(), 2)

    def test_text_file(self):
        self.assertEqual(extract_pages("a.txt", b"Hello\r\n\r\n\r\n\r\nWorld", 10), ["Hello\n\nWorld"])


class RetrievalTests(TestCase):
    def setUp(self):
        self.sample = ingest.ensure_sample()
        self.chunks = list(Chunk.objects.filter(document=self.sample).select_related("document"))

    def test_sample_has_four_pages(self):
        self.assertEqual(self.sample.pages, 4)
        self.assertEqual(Document.objects.filter(is_sample=True).count(), 1)
        ingest.ensure_sample()
        self.assertEqual(Document.objects.filter(is_sample=True).count(), 1)

    def test_finds_the_right_page(self):
        cases = {
            "How many days of annual leave do I get?": (2, "28 days"),
            "What is the hotel limit in Zurich?": (3, "220 euros"),
            "What should I do if my laptop is stolen?": (4, "two hours"),
            "How many days can I work remotely?": (1, "three days a week"),
        }
        for question, (page, phrase) in cases.items():
            hits = search(question, self.chunks, 6)
            self.assertTrue(hits, question)
            self.assertEqual(hits[0].chunk.page, page, question)
            self.assertIn(phrase, hits[0].chunk.text, question)

    def test_unrelated_question_returns_nothing(self):
        self.assertEqual(search("quantum chromodynamics lattice", self.chunks, 6), [])
        self.assertEqual(search("the of and", self.chunks, 6), [])

    def test_stemming(self):
        self.assertEqual(tokenize("Refunds returning policies"), ["refund", "return", "policy"])


class AnswerTests(TestCase):
    def setUp(self):
        sample = ingest.ensure_sample()
        chunks = list(Chunk.objects.filter(document=sample).select_related("document"))
        self.hits = search("How many days of annual leave do I get?", chunks, 3)

    def test_citations_become_numbered_sources(self):
        quote = "Full-time employees receive 28 days of paid annual leave per calendar year"
        content = [
            SimpleNamespace(type="thinking", thinking=""),
            text_block("You get "),
            text_block("28 days of paid annual leave", [citation(0, quote), citation(0, quote)]),
            text_block(", plus public holidays."),
        ]
        answer = parse_response(content, self.hits)
        self.assertEqual([s["sources"] for s in answer.segments], [[], [1], []])
        self.assertEqual(len(answer.sources), 1)
        self.assertEqual(answer.sources[0].page, 2)
        self.assertEqual(answer.sources[0].quote, quote)
        self.assertEqual(answer.as_dict()["text"], "You get 28 days of paid annual leave, plus public holidays.")

    def test_out_of_range_citation_is_ignored(self):
        answer = parse_response([text_block("x", [citation(99, "nope")])], self.hits)
        self.assertEqual(answer.sources, [])

    def test_request_sends_one_cited_document_per_passage(self):
        client = FakeClient([text_block("Answer.")])
        history = [{"question": "Earlier?", "answer": "Earlier answer."}]
        answer = answer_question("How much leave?", self.hits, history, client=client)
        self.assertEqual(answer.mode, "ai")
        messages = client.kwargs["messages"]
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user"])
        blocks = messages[-1]["content"]
        documents = [b for b in blocks if b["type"] == "document"]
        self.assertEqual(len(documents), len(self.hits))
        self.assertTrue(all(d["citations"] == {"enabled": True} for d in documents))
        self.assertTrue(all(d["source"]["media_type"] == "text/plain" for d in documents))
        self.assertIn("page 2", documents[0]["title"])
        self.assertEqual(blocks[-1], {"type": "text", "text": "How much leave?"})
        self.assertNotIn("temperature", client.kwargs)

    def test_refusal_and_no_hits(self):
        refused = answer_question("q", self.hits, client=FakeClient([], stop_reason="refusal"))
        self.assertEqual(refused.mode, "error")
        self.assertEqual(answer_question("q", [], client=FakeClient([])).mode, "no_match")

    @override_settings(ANTHROPIC_API_KEY="")
    def test_without_api_key_shows_passages(self):
        answer = answer_question("q", self.hits)
        self.assertEqual(answer.mode, "retrieval_only")
        self.assertTrue(answer.sources)


@override_settings(CACHES=LOCMEM, ANTHROPIC_API_KEY="")
class ApiTests(TestCase):
    def setUp(self):
        cache.clear()

    def _upload(self, name="policy.txt", body=b"Refunds are issued within 14 days of purchase."):
        return self.client.post("/api/documents", {"file": _named(body, name)})

    def test_page_and_health(self):
        self.assertContains(self.client.get("/"), "AskDocs")
        self.assertEqual(self.client.get("/healthz").json(), {"ok": True, "ai": False})

    def test_upload_ask_delete_flow(self):
        self.assertEqual([d["sample"] for d in self.client.get("/api/documents").json()["documents"]], [True])
        created = self._upload()
        self.assertEqual(created.status_code, 201)
        doc_id = created.json()["document"]["id"]
        self.assertEqual(len(self.client.get("/api/documents").json()["documents"]), 2)

        reply = self.client.post("/api/ask", json.dumps({"question": "When are refunds issued?"}), content_type="application/json").json()
        self.assertEqual(reply["mode"], "retrieval_only")
        self.assertEqual(reply["sources"][0]["document"], "policy.txt")

        self.assertEqual(self.client.post(f"/api/documents/{doc_id}/delete").status_code, 200)
        self.assertEqual(len(self.client.get("/api/documents").json()["documents"]), 1)

    def test_documents_are_private_to_the_session(self):
        doc_id = self._upload().json()["document"]["id"]
        other = self.client_class()
        self.assertEqual(len(other.get("/api/documents").json()["documents"]), 1)
        self.assertEqual(other.post(f"/api/documents/{doc_id}/delete").status_code, 404)
        reply = other.post("/api/ask", json.dumps({"question": "When are refunds issued?"}), content_type="application/json").json()
        self.assertNotIn("policy.txt", json.dumps(reply))

    def test_sample_cannot_be_deleted(self):
        sample_id = self.client.get("/api/documents").json()["documents"][0]["id"]
        self._upload()
        self.assertEqual(self.client.post(f"/api/documents/{sample_id}/delete").status_code, 404)

    def test_bad_requests(self):
        self.assertEqual(self.client.post("/api/documents").status_code, 400)
        self.assertEqual(self._upload(name="x.exe").status_code, 400)
        self.assertEqual(self.client.post("/api/ask", "{}", content_type="application/json").status_code, 400)
        long_q = json.dumps({"question": "a" * 601})
        self.assertEqual(self.client.post("/api/ask", long_q, content_type="application/json").status_code, 400)

    def test_document_limit(self):
        with self.settings(ASKDOCS={**_cfg(), "MAX_DOCS_PER_WORKSPACE": 1}):
            self.assertEqual(self._upload().status_code, 201)
            self.assertEqual(self._upload(name="second.txt").status_code, 400)

    def test_question_rate_limit(self):
        with self.settings(ASKDOCS={**_cfg(), "QUESTIONS_PER_IP_PER_DAY": 2}):
            body = json.dumps({"question": "annual leave"})
            codes = [self.client.post("/api/ask", body, content_type="application/json").status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])

    def test_expired_workspaces_are_purged(self):
        ingest.ensure_sample()
        self._upload()
        Workspace.objects.update(created_at="2020-01-01T00:00:00Z")
        ingest.purge_expired()
        self.assertEqual(Workspace.objects.count(), 0)
        self.assertEqual(Document.objects.filter(is_sample=False).count(), 0)
        self.assertEqual(Document.objects.filter(is_sample=True).count(), 1)


def _named(body: bytes, name: str):
    handle = io.BytesIO(body)
    handle.name = name
    return handle


def _cfg():
    from django.conf import settings

    return dict(settings.ASKDOCS)
