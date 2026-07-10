#!/usr/bin/env python3
"""Encode chunks.jsonl into embeddings.npy using BGE-M3 (CPU, normalize L2).

Pipeline stage (Brique 2). Reads ``corpus/chunks.jsonl``, filters
micro-chunks below MIN_TOKENS (REQ-CHUNK-04), encodes with
sentence-transformers. Writes two files sharing row order:

    corpus/embeddings.npy         — float32 matrix (N_kept, EMBED_DIM)
    corpus/embeddings_index.jsonl — one metadata line per row (same
                                    schema as chunks.jsonl)

Determinism is intra-machine only (CPU, model eval mode). Cross-machine
bit-for-bit is *not* guaranteed for neural outputs — the manifest hash
is informational, not a bit-for-bit contract. The retrieve stage does
matmul + argsort on the matrix.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import tiktoken

REPO_ROOT = Path(__file__).resolve().parent
CHUNKS_JSONL_PATH = REPO_ROOT / "corpus" / "chunks.jsonl"
EMBEDDINGS_NPY_PATH = REPO_ROOT / "corpus" / "embeddings.npy"
EMBEDDINGS_INDEX_PATH = REPO_ROOT / "corpus" / "embeddings_index.jsonl"

MODEL_NAME = "BAAI/bge-m3"
# HF commit SHA pinned — NOT ``main``. ``main`` is a mutable branch and
# BAAI can push a new weight file at any moment, silently invalidating
# the ``producer_env`` block declared in ``corpus/manifest.yaml``. The
# whole point of the harness is to catch this class of silent drift on
# the substrate it evaluates — pinning ourselves is table stakes.
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DEVICE = "cpu"
BATCH_SIZE = 32
NORMALIZE_EMBEDDINGS = True
EMBED_DIM = 1024
DTYPE = np.float32

# REQ-CHUNK-04 — le retriever exclut de l'index les chunks dont
# token_count(text, cl100k_base) < MIN_TOKENS. Micro-chunks = headers
# répétés, artefacts d'extraction, pages quasi-vides : bruit cosinus
# sans signal utile. Les chunks filtrés restent dans chunks.jsonl
# (audit préservé), ils ne sont simplement pas indexés.
MIN_TOKENS = 10

_TOKENIZER = tiktoken.get_encoding("cl100k_base")


def _token_count(text: str) -> int:
    return len(_TOKENIZER.encode(text))


def load_chunks(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file, return list of dicts. Blank lines ignored."""
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line:
                out.append(json.loads(line))
    return out


def filter_indexable(
    chunks: list[dict[str, Any]], min_tokens: int
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(kept, dropped_count)``. ``kept`` preserves input order.

    Applies REQ-CHUNK-04 : chunks with ``token_count(text) < min_tokens``
    are excluded from the retrieval index.
    """
    kept = [c for c in chunks if _token_count(c["text"]) >= min_tokens]
    return kept, len(chunks) - len(kept)


def encode_chunks(chunks: list[dict[str, Any]], model) -> np.ndarray:
    """Encode chunk texts into an ``(N, EMBED_DIM)`` float32 L2-normalized matrix.

    Batch size 32; ``normalize_embeddings=True`` so cosine similarity
    reduces to a plain dot product downstream. Dtype forced to float32 —
    BGE-M3 emits float32 natively but the cast makes it explicit.
    """
    texts = [c["text"] for c in chunks]
    vectors = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        normalize_embeddings=NORMALIZE_EMBEDDINGS,
        convert_to_numpy=True,
        show_progress_bar=True,
    )
    return vectors.astype(DTYPE)


def write_matrix(matrix: np.ndarray, path: Path) -> None:
    """Atomic write of a ``.npy`` file via ``.tmp`` sidecar.

    Passing a file handle (not a path) to ``np.save`` avoids the
    surprising automatic ``.npy`` suffix append.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("wb") as fh:
            np.save(fh, matrix, allow_pickle=False)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_index(chunks: list[dict[str, Any]], path: Path) -> None:
    """Atomic write of the embeddings_index.jsonl mirror of kept chunks.

    Same schema as ``chunks.jsonl``. Row ``i`` here == row ``i`` in
    the matrix.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            for c in chunks:
                fh.write(json.dumps(c, ensure_ascii=False))
                fh.write("\n")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def main() -> int:
    if not CHUNKS_JSONL_PATH.exists():
        print(
            f"ERROR: {CHUNKS_JSONL_PATH} missing. Run chunk_pages.py first.",
            file=sys.stderr,
        )
        return 1

    print(f"Loading chunks from {CHUNKS_JSONL_PATH}")
    chunks = load_chunks(CHUNKS_JSONL_PATH)
    kept, dropped = filter_indexable(chunks, MIN_TOKENS)
    print(
        f"  loaded={len(chunks)}, kept={len(kept)}, "
        f"dropped(<{MIN_TOKENS} tok)={dropped}"
    )

    if not kept:
        print("ERROR: no chunk left to index.", file=sys.stderr)
        return 1

    # Imported here so the CLI's error paths above stay fast — the
    # SentenceTransformer import triggers a torch load (~3s).
    from sentence_transformers import SentenceTransformer

    print(f"Loading model {MODEL_NAME} (revision={MODEL_REVISION}, device={DEVICE})")
    model = SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION, device=DEVICE)
    model.eval()

    print(f"Encoding {len(kept)} chunk(s)...")
    matrix = encode_chunks(kept, model)
    assert matrix.shape == (len(kept), EMBED_DIM), (
        f"unexpected shape {matrix.shape}, expected ({len(kept)}, {EMBED_DIM})"
    )
    assert matrix.dtype == DTYPE

    write_matrix(matrix, EMBEDDINGS_NPY_PATH)
    write_index(kept, EMBEDDINGS_INDEX_PATH)
    print(f"\nWrote {EMBEDDINGS_NPY_PATH} ({matrix.shape} {matrix.dtype})")
    print(f"Wrote {EMBEDDINGS_INDEX_PATH} ({len(kept)} rows)")

    # Print SHA256 for traceability — the manifest hash is informational
    # (not a bit-for-bit contract, since neural output is hardware-dependent),
    # so the update is a manual step after each build. Printing here saves
    # the operator from re-running ``sha256sum`` by hand.
    for path in (EMBEDDINGS_NPY_PATH, EMBEDDINGS_INDEX_PATH):
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"  sha256({path.name}) = {h}  ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
