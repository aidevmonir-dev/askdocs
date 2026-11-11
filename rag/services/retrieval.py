"""BM25 keyword retrieval, implemented in plain Python.

A workspace holds at most a few hundred passages, so ranking them in memory per question is fast
and keeps the project free of a vector database, an embeddings bill and a GPU. The interface is a
single function, so swapping in embeddings later only touches this file.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from ..models import Chunk

K1 = 1.5
B = 0.75

_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?", re.IGNORECASE)
_STOPWORDS = frozenset(
    """a an and are as at be but by can do does for from had has have how i if in is it its me my of on or
    our so than that the their them then there these they this to was we were what when where which who
    whom why will with would you your about into over under please tell say""".split()
)


def tokenize(text: str) -> list[str]:
    tokens = []
    for raw in _TOKEN.findall(text.lower()):
        if raw in _STOPWORDS or len(raw) < 2:
            continue
        # Light stemming: enough to match "refund" with "refunds" and "returning" with "return".
        for suffix in ("ing", "ies", "es", "ed", "s"):
            if raw.endswith(suffix) and len(raw) - len(suffix) >= 3:
                raw = raw[: -len(suffix)] + ("y" if suffix == "ies" else "")
                break
        tokens.append(raw)
    return tokens


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


def search(question: str, chunks: list[Chunk], top_k: int) -> list[Hit]:
    """Rank passages against the question. Returns at most top_k hits, best first, with score > 0."""
    query_terms = set(tokenize(question))
    if not query_terms or not chunks:
        return []

    tokenized = [tokenize(c.text) for c in chunks]
    doc_count = len(tokenized)
    avg_len = sum(len(t) for t in tokenized) / doc_count or 1.0
    doc_freq: Counter[str] = Counter()
    for tokens in tokenized:
        doc_freq.update(set(tokens) & query_terms)

    hits = []
    for chunk, tokens in zip(chunks, tokenized):
        if not tokens:
            continue
        freqs = Counter(tokens)
        score = 0.0
        for term in query_terms:
            tf = freqs.get(term, 0)
            if not tf:
                continue
            idf = math.log(1 + (doc_count - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            score += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * len(tokens) / avg_len))
        if score > 0:
            hits.append(Hit(chunk, score))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]
