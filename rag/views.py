from __future__ import annotations

import json
import logging

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import limits
from .models import Chunk, Document, Workspace
from .services import ingest
from .services.answer import answer_question
from .services.extract import ExtractionError
from .services.retrieval import search

log = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 600
MAX_HISTORY_ANSWER_CHARS = 2000


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _workspace(request: HttpRequest, create: bool = False) -> Workspace | None:
    workspace_id = request.session.get("workspace")
    if workspace_id:
        workspace = Workspace.objects.filter(pk=workspace_id).first()
        if workspace:
            return workspace
    if not create:
        return None
    workspace = Workspace.objects.create()
    request.session["workspace"] = str(workspace.pk)
    return workspace


def _visible_documents(workspace: Workspace | None):
    sample = ingest.ensure_sample()
    documents = [sample]
    if workspace:
        documents += list(workspace.documents.all())
    return documents


def _serialize(document: Document) -> dict:
    return {"id": document.pk, "name": document.name, "pages": document.pages, "sample": document.is_sample}


@ensure_csrf_cookie
@require_GET
def index(request: HttpRequest):
    cfg = settings.ASKDOCS
    return render(
        request,
        "rag/index.html",
        {
            "max_mb": cfg["MAX_FILE_BYTES"] // (1024 * 1024),
            "max_pages": cfg["MAX_PAGES"],
            "max_docs": cfg["MAX_DOCS_PER_WORKSPACE"],
            "retention_hours": cfg["RETENTION_HOURS"],
            "repo_url": cfg["REPO_URL"],
            "ai_enabled": bool(settings.ANTHROPIC_API_KEY),
        },
    )


@require_http_methods(["GET", "POST"])
def documents(request: HttpRequest):
    if request.method == "GET":
        return JsonResponse({"documents": [_serialize(d) for d in _visible_documents(_workspace(request))]})

    upload = request.FILES.get("file")
    if upload is None:
        return _error("Choose a file to upload.")
    if not limits.allow("upload", limits.client_ip(request), settings.ASKDOCS["UPLOADS_PER_IP_PER_DAY"]):
        return _error("You have reached today's upload limit for this demo. Try again tomorrow.", 429)
    if upload.size > settings.ASKDOCS["MAX_FILE_BYTES"]:
        return _error(f"This file is larger than {settings.ASKDOCS['MAX_FILE_BYTES'] // (1024 * 1024)} MB.")

    ingest.purge_expired()
    workspace = _workspace(request, create=True)
    try:
        document = ingest.ingest_upload(upload.name, upload.read(), workspace)
    except ExtractionError as exc:
        return _error(str(exc))
    return JsonResponse({"document": _serialize(document)}, status=201)


@require_POST
def delete_document(request: HttpRequest, pk: int):
    workspace = _workspace(request)
    # Scoped to the visitor's own workspace, so nobody can delete the sample or another visitor's file.
    deleted = workspace.documents.filter(pk=pk).delete()[0] if workspace else 0
    if not deleted:
        return _error("That document was not found.", 404)
    return JsonResponse({"ok": True})


@require_POST
def ask(request: HttpRequest):
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("Send the question as JSON.")
    question = str(payload.get("question", "")).strip()
    if not question:
        return _error("Type a question first.")
    if len(question) > MAX_QUESTION_CHARS:
        return _error(f"Keep the question under {MAX_QUESTION_CHARS} characters.")

    cfg = settings.ASKDOCS
    if not limits.allow("ask", limits.client_ip(request), cfg["QUESTIONS_PER_IP_PER_DAY"]):
        return _error("You have reached today's question limit for this demo. Try again tomorrow.", 429)
    if not limits.allow("ask-total", "everyone", cfg["QUESTIONS_PER_DAY_TOTAL"]):
        return _error("This demo has reached its daily limit. Try again tomorrow.", 429)

    history = []
    for turn in (payload.get("history") or [])[-3:]:
        if isinstance(turn, dict) and turn.get("question") and turn.get("answer"):
            history.append(
                {"question": str(turn["question"])[:MAX_QUESTION_CHARS], "answer": str(turn["answer"])[:MAX_HISTORY_ANSWER_CHARS]}
            )

    document_ids = [d.pk for d in _visible_documents(_workspace(request))]
    chunks = list(Chunk.objects.filter(document_id__in=document_ids).select_related("document"))
    # A follow-up like "and for contractors?" only makes sense with the previous question's words.
    search_text = question if not history else f"{history[-1]['question']} {question}"
    hits = search(search_text, chunks, cfg["TOP_K"])
    return JsonResponse(answer_question(question, hits, history).as_dict())


@require_GET
def healthz(request: HttpRequest):
    return JsonResponse({"ok": True, "ai": bool(settings.ANTHROPIC_API_KEY)})
