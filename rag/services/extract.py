"""Turn an uploaded file into a list of page texts. Nothing is written to disk."""
from __future__ import annotations

import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

TEXT_EXTENSIONS = {".txt", ".md"}
ALLOWED_EXTENSIONS = TEXT_EXTENSIONS | {".pdf"}


class ExtractionError(Exception):
    """Raised with a message that is safe to show the visitor."""


def _clean(text: str) -> str:
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pages(filename: str, data: bytes, max_pages: int) -> list[str]:
    """Return one cleaned text string per page (a text file is a single page)."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise ExtractionError("Upload a PDF, .txt or .md file.")

    if ext in TEXT_EXTENSIONS:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        pages = [_clean(text)]
    else:
        if not data.startswith(b"%PDF"):
            raise ExtractionError("This file is not a valid PDF.")
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ExtractionError("This PDF is password protected. Remove the password and upload it again.")
            if len(reader.pages) > max_pages:
                raise ExtractionError(f"This PDF has {len(reader.pages)} pages. The limit is {max_pages}.")
            pages = [_clean(page.extract_text() or "") for page in reader.pages]
        except ExtractionError:
            raise
        except (PdfReadError, ValueError, KeyError, OSError) as exc:
            raise ExtractionError("This PDF could not be read. It may be damaged.") from exc

    if not any(pages):
        raise ExtractionError(
            "No text was found in this file. Scanned PDFs need OCR first, which this demo does not include."
        )
    return pages
