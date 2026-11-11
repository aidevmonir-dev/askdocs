"""Store a document as passages, load the built-in sample, and remove expired workspaces."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import Chunk, Document, Workspace
from .chunking import chunk_pages
from .extract import ExtractionError, extract_pages

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "sample" / "northwind-handbook.md"
SAMPLE_NAME = "Northwind employee handbook (sample)"


@transaction.atomic
def store_document(name: str, pages: list[str], workspace: Workspace | None, is_sample: bool = False) -> Document:
    passages = chunk_pages(pages)
    document = Document.objects.create(
        workspace=workspace,
        name=name[:200],
        pages=len(pages),
        chars=sum(len(p) for p in pages),
        is_sample=is_sample,
    )
    Chunk.objects.bulk_create(
        Chunk(document=document, page=p.page, ordinal=i, text=p.text) for i, p in enumerate(passages)
    )
    return document


def ingest_upload(filename: str, data: bytes, workspace: Workspace) -> Document:
    limits = settings.ASKDOCS
    if len(data) > limits["MAX_FILE_BYTES"]:
        raise ExtractionError(f"This file is larger than {limits['MAX_FILE_BYTES'] // (1024 * 1024)} MB.")
    if workspace.documents.count() >= limits["MAX_DOCS_PER_WORKSPACE"]:
        raise ExtractionError(f"You can keep {limits['MAX_DOCS_PER_WORKSPACE']} documents at a time. Remove one first.")
    pages = extract_pages(filename, data, limits["MAX_PAGES"])
    if sum(len(p) for p in pages) > limits["MAX_CHARS"]:
        raise ExtractionError("This document has too much text for the demo. Try a shorter one.")
    return store_document(filename, pages, workspace)


def ensure_sample() -> Document:
    """The sample handbook is shared by every visitor, so the demo works before anyone uploads."""
    sample = Document.objects.filter(is_sample=True).first()
    if sample is None:
        # The file marks page breaks with a form feed so the sample shows page-level citations.
        pages = [p.strip() for p in SAMPLE_PATH.read_text(encoding="utf-8").split("\f") if p.strip()]
        sample = store_document(SAMPLE_NAME, pages, workspace=None, is_sample=True)
    return sample


def purge_expired() -> int:
    """Delete workspaces (and, by cascade, their documents and passages) past the retention window."""
    cutoff = timezone.now() - timedelta(hours=settings.ASKDOCS["RETENTION_HOURS"])
    deleted, _ = Workspace.objects.filter(created_at__lt=cutoff).delete()
    return deleted
