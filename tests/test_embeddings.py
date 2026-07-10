"""Tests IVVQ Brique 2 — embeddings + retrieval.

**Posture produit** (voir `feedback-posture-produit` en mémoire) : on vérifie
les **propriétés fonctionnelles** de l'artefact committé (dim, norm L2, count,
ordre, schema), pas un SHA256 bit-à-bit. Les embeddings sont
hardware-dépendants — figer les bytes serait un faux contrat qui pèterait
au premier changement de machine.

Ce qu'on garantit ici :
- REQ-CHUNK-04 : le filtre micro-chunks est appliqué correctement.
- REQ-EMBED-01 : dim = 1024, dtype = float32, matrix aligned avec l'index.
- REQ-EMBED-02 : vecteurs L2-normalisés (contrat pour retrieve = dot product).
- REQ-RETRIEVE-01 : retrieve renvoie top-k triés desc, scores dans [-1, 1].
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

import build_embeddings
import retrieve as retrieve_mod
from build_embeddings import (
    CHUNKS_JSONL_PATH,
    EMBED_DIM,
    EMBEDDINGS_INDEX_PATH,
    EMBEDDINGS_NPY_PATH,
    MIN_TOKENS,
    _token_count,
    filter_indexable,
    load_chunks,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "corpus" / "manifest.yaml"


# ---------------------------------------------------------------------------
# Unit tests — REQ-CHUNK-04 filter behavior
# ---------------------------------------------------------------------------


def _fake_chunk(text: str, chunk_idx: int = 0) -> dict:
    """Minimal chunk dict with just what filter_indexable needs."""
    return {
        "doc_id": "fake",
        "page_num": 1,
        "chunk_idx": chunk_idx,
        "char_start": 0,
        "char_end": len(text),
        "text": text,
    }


def test_filter_indexable_drops_chunks_below_min_tokens() -> None:
    micro = _fake_chunk("court", 0)  # 1-2 tokens cl100k
    ok = _fake_chunk(
        "Une phrase suffisamment longue pour dépasser le seuil de dix "
        "tokens de manière confortable dans cl100k.",
        1,
    )
    kept, dropped = filter_indexable([micro, ok], MIN_TOKENS)
    assert dropped == 1
    assert len(kept) == 1
    assert kept[0]["chunk_idx"] == 1


def test_filter_indexable_preserves_input_order() -> None:
    long_a = _fake_chunk("Première phrase longue " * 10, 0)
    micro = _fake_chunk("x", 1)
    long_b = _fake_chunk("Seconde phrase longue " * 10, 2)
    kept, _ = filter_indexable([long_a, micro, long_b], MIN_TOKENS)
    assert [c["chunk_idx"] for c in kept] == [0, 2]


def test_filter_indexable_reports_dropped_count() -> None:
    chunks = [_fake_chunk("x", i) for i in range(5)]
    kept, dropped = filter_indexable(chunks, MIN_TOKENS)
    assert kept == []
    assert dropped == 5


def test_filter_indexable_empty_input() -> None:
    kept, dropped = filter_indexable([], MIN_TOKENS)
    assert kept == []
    assert dropped == 0


def test_filter_indexable_boundary_at_min_tokens() -> None:
    """Boundary check : ``token_count == MIN_TOKENS`` → kept ; ``< MIN_TOKENS`` → dropped.

    Locks the ``>=`` comparator against a refactor to ``>`` that would
    silently drop one boundary chunk per doc.
    """
    # Build texts of exactly MIN_TOKENS and MIN_TOKENS - 1 tokens by
    # appending single-token words.
    single_token_word = "mot"
    at_boundary_text = " ".join([single_token_word] * MIN_TOKENS)
    below_boundary_text = " ".join([single_token_word] * (MIN_TOKENS - 1))
    assert _token_count(at_boundary_text) >= MIN_TOKENS
    assert _token_count(below_boundary_text) < MIN_TOKENS

    at = _fake_chunk(at_boundary_text, 0)
    below = _fake_chunk(below_boundary_text, 1)
    kept, dropped = filter_indexable([at, below], MIN_TOKENS)
    assert [c["chunk_idx"] for c in kept] == [0]
    assert dropped == 1


# ---------------------------------------------------------------------------
# Committed-artifact tests — REQ-EMBED-01, REQ-EMBED-02
# ---------------------------------------------------------------------------

_ARTIFACTS_PRESENT = (
    EMBEDDINGS_NPY_PATH.exists() and EMBEDDINGS_INDEX_PATH.exists()
)
_requires_artifacts = pytest.mark.skipif(
    not _ARTIFACTS_PRESENT,
    reason="run `python build_embeddings.py` first to produce corpus/embeddings.npy",
)


@_requires_artifacts
def test_committed_embeddings_has_expected_dim_and_dtype() -> None:
    matrix = np.load(EMBEDDINGS_NPY_PATH, allow_pickle=False)
    assert matrix.ndim == 2
    assert matrix.shape[1] == EMBED_DIM
    assert matrix.dtype == np.float32


@_requires_artifacts
def test_committed_embeddings_are_l2_normalized() -> None:
    """REQ-EMBED-02 — every row has ‖v‖₂ ≈ 1 within 1e-5.

    Contract that lets ``retrieve`` treat ``matrix @ query`` as cosine
    similarity (a plain dot product). If this drifts, every score is
    silently wrong.
    """
    matrix = np.load(EMBEDDINGS_NPY_PATH, allow_pickle=False)
    norms = np.linalg.norm(matrix, axis=1)
    max_dev = float(np.max(np.abs(norms - 1.0)))
    assert max_dev < 1e-5, f"L2 norm max deviation = {max_dev:.2e}"


@_requires_artifacts
def test_committed_matrix_rows_match_index_rows() -> None:
    matrix = np.load(EMBEDDINGS_NPY_PATH, allow_pickle=False)
    with EMBEDDINGS_INDEX_PATH.open("r", encoding="utf-8") as fh:
        n_index = sum(1 for line in fh if line.strip())
    assert matrix.shape[0] == n_index


@_requires_artifacts
def test_committed_index_schema_matches_chunks_jsonl() -> None:
    expected_keys = {
        "doc_id",
        "page_num",
        "chunk_idx",
        "char_start",
        "char_end",
        "text",
    }
    with EMBEDDINGS_INDEX_PATH.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            raw = raw.rstrip("\n")
            if not raw:
                continue
            row = json.loads(raw)
            assert set(row.keys()) == expected_keys, (
                f"line {line_no}: keys mismatch"
            )


@_requires_artifacts
def test_committed_index_is_ordered_subset_of_chunks_jsonl() -> None:
    """Every index row must match a chunks.jsonl row dict-for-dict, in order.

    Full-dict equality (not just ``(doc_id, page_num, chunk_idx)``) so a
    silent transform of ``text``, ``char_start`` or ``char_end`` between
    ``chunks.jsonl`` and ``embeddings_index.jsonl`` is caught. Downstream
    citations in the VCD (B7) rely on the index row being byte-identical
    to the source chunk.
    """
    source_chunks = load_chunks(CHUNKS_JSONL_PATH)

    with EMBEDDINGS_INDEX_PATH.open("r", encoding="utf-8") as fh:
        index_rows = [json.loads(line) for line in fh if line.strip()]

    cursor = 0
    for row in index_rows:
        # walk forward in source until full-dict match
        while cursor < len(source_chunks) and source_chunks[cursor] != row:
            cursor += 1
        assert cursor < len(source_chunks), (
            f"index row {(row['doc_id'], row['page_num'], row['chunk_idx'])} "
            f"has no dict-equal counterpart in chunks.jsonl (or out of order)"
        )
        cursor += 1


# ---------------------------------------------------------------------------
# Verrous producer_env — REQ-EMBED-01, REQ-CHUNK-04
# ---------------------------------------------------------------------------


def _load_manifest_producer_env() -> dict:
    with MANIFEST_PATH.open("r", encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh)
    return manifest["derived_artifacts"]["embeddings_npy"]["producer_env"]


def test_min_tokens_filter_matches_manifest() -> None:
    """REQ-CHUNK-04 citation integrity — code constant == manifest producer_env.

    The one verrou we deliberately keep from the B1 pattern
    (``test_manifest_producer_env_matches_module_constants``) : ``MIN_TOKENS``
    governs the *shape* of ``embeddings_index.jsonl`` (which chunks are
    in / out of the retrieval scope). A drift where code moves to
    ``MIN_TOKENS = 15`` while the manifest still declares ``10`` would
    produce an artifact that passes every structural test — smaller but
    still an ordered subset — and would silently misalign REQ-CHUNK-04
    citations in the future VCD.
    """
    producer_env = _load_manifest_producer_env()
    assert producer_env["min_tokens_filter"] == MIN_TOKENS


def test_model_revision_pinned_and_consistent() -> None:
    """REQ-EMBED-01 — model revision is a commit SHA (not ``main``) and
    matches across ``build_embeddings``, ``retrieve``, and the manifest.

    A mutable ``main`` here would let BAAI push a new weight file and
    silently invalidate the producer_env — exactly the drift the harness
    exists to catch. This test refuses that fake contract.
    """
    producer_env = _load_manifest_producer_env()
    manifest_rev = producer_env["revision"]

    assert manifest_rev != "main", (
        "revision must be pinned to a commit SHA, not the mutable 'main' branch"
    )
    assert len(manifest_rev) == 40 and all(
        c in "0123456789abcdef" for c in manifest_rev
    ), f"revision {manifest_rev!r} does not look like a 40-char hex SHA"

    assert build_embeddings.MODEL_REVISION == manifest_rev
    assert retrieve_mod.MODEL_REVISION == manifest_rev
    assert build_embeddings.MODEL_NAME == producer_env["model"]
    assert retrieve_mod.MODEL_NAME == producer_env["model"]


# ---------------------------------------------------------------------------
# Integration tests — REQ-RETRIEVE-01 (loads BGE-M3, ~15s cold)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@_requires_artifacts
def test_retrieve_returns_top_k_sorted_desc() -> None:
    from retrieve import retrieve

    results = retrieve("Quelles sont les recommandations MFA de l'ANSSI ?", k=4)
    assert len(results) == 4
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)
    for r in results:
        assert -1.0 <= r.score <= 1.0
        assert r.text  # non-empty


@_requires_artifacts
def test_retrieve_k_zero_returns_empty() -> None:
    from retrieve import retrieve

    assert retrieve("anything", k=0) == []


@pytest.mark.integration
@_requires_artifacts
@pytest.mark.parametrize(
    "question, expected_doc_id",
    [
        ("Quelles sont les recommandations sur les mots de passe et le MFA ?", "mfa"),
        ("Comment sécuriser l'administration d'un Active Directory ?", "active-directory"),
        ("Quelles sont les étapes de la méthode EBIOS Risk Manager ?", "ebios-rm"),
    ],
)
def test_retrieve_top_k_contains_expected_doc(
    question: str, expected_doc_id: str
) -> None:
    """Semantic non-regression anchor — the expected doc must appear in top-3.

    Closes the row-content-vs-row-alignment gap : the structural tests
    confirm ``matrix.shape[0] == len(index)``, but a silent bug that
    shuffles the matrix rows (batching regression, cache collision,
    dedup) would pass all structural tests and return nonsense at
    retrieval time. This test catches that class of drift by verifying
    the retriever routes each query to the correct source document at
    all — using top-k=3 rather than top-1 so cross-document overlap
    (e.g. AD-hardening advice appearing in the hygiene guide) does not
    make the anchor brittle. Fine-grained top-1 precision is a
    retrieval-quality question that belongs in the B5/B7 bench, not in
    this row-alignment guard.
    """
    from retrieve import retrieve

    results = retrieve(question, k=3)
    top_docs = [r.doc_id for r in results]
    assert expected_doc_id in top_docs, (
        f"top-3 for {question!r} = {top_docs}, "
        f"expected {expected_doc_id!r} to appear"
    )
