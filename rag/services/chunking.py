"""Split page text into passages small enough to retrieve precisely and large enough to keep context."""
from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_CHARS = 900
MAX_CHARS = 1300

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


@dataclass(frozen=True)
class Passage:
    page: int  # 1-based page number
    text: str


def _units(page_text: str) -> list[str]:
    """Paragraphs first; long paragraphs are split into sentences, and overlong sentences are hard-cut."""
    units: list[str] = []
    for para in re.split(r"\n\s*\n", page_text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= MAX_CHARS:
            units.append(para)
            continue
        for sentence in _SENTENCE_END.split(para):
            sentence = sentence.strip()
            while len(sentence) > MAX_CHARS:
                cut = sentence.rfind(" ", 0, MAX_CHARS)
                cut = cut if cut > MAX_CHARS // 2 else MAX_CHARS
                units.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if sentence:
                units.append(sentence)
    return units


def chunk_pages(pages: list[str]) -> list[Passage]:
    """Greedy packing of paragraphs/sentences into passages. A passage never crosses a page boundary,
    so every citation can name an exact page."""
    passages: list[Passage] = []
    for number, page_text in enumerate(pages, start=1):
        current = ""
        for unit in _units(page_text):
            if current and len(current) + len(unit) + 2 > MAX_CHARS:
                passages.append(Passage(number, current))
                current = ""
            current = f"{current}\n\n{unit}" if current else unit
            if len(current) >= TARGET_CHARS:
                passages.append(Passage(number, current))
                current = ""
        if current:
            passages.append(Passage(number, current))
    return passages
