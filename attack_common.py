#!/usr/bin/env python3
"""Shared wiring for corpus_attack/ demo scripts — Brique 6.

Extracted from ``demo_injection.py`` (Brique 4) when the leak demo
(``demo_leak.py``, REQ-LEAK-01) needed the exact same benign ∪ attack
union-retrieval wiring. Duplicating it a second time would let the two
OWASP demos silently drift apart on how the attack chunk is embedded or
how top-k is computed over the union — exactly the class of divergence
this project refuses to leave undetected. Keeping one copy means a
change here applies to every attack demo by construction.

Not attack-specific in what it does — it has no knowledge of any
payload or verdict — only in why it exists (every current caller is a
corpus_attack/ demo).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from build_embeddings import encode_chunks
from retrieve import RetrievalResult, _load_model, encode_query


def fake_chunk_from_markdown(path: Path, doc_id: str) -> dict:
    """One chunk covering the whole fake ``.md`` — attack docs are short by design."""
    text = path.read_text(encoding="utf-8")
    return {
        "doc_id": doc_id,
        "page_num": 1,
        "chunk_idx": 0,
        "char_start": 0,
        "char_end": len(text),
        "text": text,
    }


def embed_attack_chunks(chunks: list[dict]) -> np.ndarray:
    """Encode attack chunks with the same pinned BGE-M3 as Brique 2.

    The shape/dtype/L2 contract is enforced by ``encode_chunks`` itself
    (Brique 2, REQ-EMBED-02) — no defensive re-assertion here.
    """
    return encode_chunks(chunks, _load_model())


def retrieve_union(
    question: str,
    benign_matrix: np.ndarray,
    benign_index: list[dict],
    attack_matrix: np.ndarray,
    attack_index: list[dict],
    k: int,
) -> list[RetrievalResult]:
    """Top-k retrieval over the concatenation of benign and attack indices."""
    matrix = np.concatenate([benign_matrix, attack_matrix], axis=0)
    index = benign_index + attack_index
    k = min(k, len(index))
    q = encode_query(question)
    scores = matrix @ q
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top])]
    return [
        RetrievalResult(
            doc_id=index[i]["doc_id"],
            page_num=index[i]["page_num"],
            chunk_idx=index[i]["chunk_idx"],
            char_start=index[i]["char_start"],
            char_end=index[i]["char_end"],
            text=index[i]["text"],
            score=float(scores[i]),
        )
        for i in top
    ]
