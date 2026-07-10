#!/usr/bin/env python3
"""Top-k semantic retrieval over the ANSSI chunk corpus.

Loads ``corpus/embeddings.npy`` + ``corpus/embeddings_index.jsonl`` (row
i in the matrix == row i in the JSONL), encodes the query with the same
BGE-M3 model, computes ``matrix @ query`` (= cosine similarity because
both sides are L2-normalized), returns the top-k :class:`RetrievalResult`
sorted by descending score.

Score is kept raw in ``[-1, 1]`` — not rescaled to ``[0, 1]``. Downstream
stages (LLM-as-judge B6, VCD B7) may need to distinguish a low-signal
near-zero match from an outright orthogonal one; normalization would
erase that.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent
EMBEDDINGS_NPY_PATH = REPO_ROOT / "corpus" / "embeddings.npy"
EMBEDDINGS_INDEX_PATH = REPO_ROOT / "corpus" / "embeddings_index.jsonl"

MODEL_NAME = "BAAI/bge-m3"
# HF commit SHA — must match ``build_embeddings.MODEL_REVISION`` and the
# ``producer_env.revision`` in ``corpus/manifest.yaml``. Enforced by
# ``test_model_revision_matches_between_builder_retriever_and_manifest``.
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DEVICE = "cpu"


@dataclass(frozen=True)
class RetrievalResult:
    """A chunk retrieved for a query, with its raw cosine score.

    Inherits all fields from the source chunk (schema of chunks.jsonl)
    plus ``score`` — the dot product of L2-normalized vectors, so
    cosine similarity in ``[-1, 1]``.
    """

    doc_id: str
    page_num: int
    chunk_idx: int
    char_start: int
    char_end: int
    text: str
    score: float


@lru_cache(maxsize=1)
def _load_matrix() -> np.ndarray:
    return np.load(EMBEDDINGS_NPY_PATH, allow_pickle=False)


@lru_cache(maxsize=1)
def _load_index() -> list[dict]:
    out: list[dict] = []
    with EMBEDDINGS_INDEX_PATH.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line:
                out.append(json.loads(line))
    return out


@lru_cache(maxsize=1)
def _load_model():
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION, device=DEVICE)
    model.eval()
    return model


def encode_query(question: str) -> np.ndarray:
    """Encode a single query into a ``(EMBED_DIM,)`` float32 L2-normalized vector."""
    model = _load_model()
    vec = model.encode(
        [question],
        normalize_embeddings=True,
        convert_to_numpy=True,
    )[0]
    return vec.astype(np.float32)


def retrieve(question: str, k: int = 4) -> list[RetrievalResult]:
    """Return the top-k chunks most similar to ``question``, descending score.

    Clamps ``k`` to the index size when the corpus is smaller than the
    requested k (small-corpus safety). Returns ``[]`` for ``k <= 0`` or
    an empty index — the latter guards against a callsite that would
    otherwise crash inside ``np.argpartition`` on a zero-length array.
    """
    if k <= 0:
        return []
    matrix = _load_matrix()
    index = _load_index()
    if len(index) == 0:
        return []
    k = min(k, len(index))
    q = encode_query(question)
    scores = matrix @ q  # (N,) — cosine because both sides are L2-normalized
    # argpartition for the top-k then argsort for the ordering — O(N + k log k)
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


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Retrieve top-k ANSSI chunks for a French query."
    )
    parser.add_argument(
        "question",
        help="French question (e.g. \"Quelles sont les recommandations MFA ?\")",
    )
    parser.add_argument(
        "-k", type=int, default=4, help="Number of results (default: 4)"
    )
    args = parser.parse_args()

    results = retrieve(args.question, k=args.k)
    for rank, r in enumerate(results, start=1):
        preview = r.text[:200].replace("\n", " ")
        print(
            f"\n[{rank}] score={r.score:.4f}  "
            f"{r.doc_id} p.{r.page_num} #{r.chunk_idx}"
        )
        print(f"    {preview}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
