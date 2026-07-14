"""Tests for the shared attack-demo wiring (Brique 6, extracted from
Brique 4's ``demo_injection.py`` when ``demo_leak.py`` needed the same
union-retrieval logic).

``retrieve_union`` is the load-bearing function this module exists to
protect from silent drift between attack demos — locked here on a tiny
synthetic matrix, no BGE-M3 load, no network. ``encode_query`` is
monkeypatched so the test stays a pure numpy check of the top-k
selection and index bookkeeping, not a retrieval-quality test.
"""

from __future__ import annotations

import numpy as np
import pytest

import attack_common


def _chunk(doc_id: str, idx: int) -> dict:
    return {
        "doc_id": doc_id,
        "page_num": 1,
        "chunk_idx": idx,
        "char_start": 0,
        "char_end": 4,
        "text": "text",
    }


@pytest.fixture(autouse=True)
def _stub_encode_query(monkeypatch):
    # retrieve_union only ever uses the query vector via `matrix @ q` —
    # a fixed unit vector keeps `scores` equal to a chosen matrix column,
    # so the expected ranking is trivial to state.
    monkeypatch.setattr(
        attack_common, "encode_query", lambda question: np.array([1.0], dtype=np.float32)
    )


def test_retrieve_union_ranks_by_descending_score():
    benign_matrix = np.array([[0.2], [0.9]], dtype=np.float32)
    benign_index = [_chunk("benign-a", 0), _chunk("benign-b", 1)]
    attack_matrix = np.array([[0.5]], dtype=np.float32)
    attack_index = [_chunk("attack-x", 0)]

    results = attack_common.retrieve_union(
        question="q",
        benign_matrix=benign_matrix,
        benign_index=benign_index,
        attack_matrix=attack_matrix,
        attack_index=attack_index,
        k=3,
    )

    assert [r.doc_id for r in results] == ["benign-b", "attack-x", "benign-a"]
    assert [r.score for r in results] == pytest.approx([0.9, 0.5, 0.2])


def test_retrieve_union_respects_k_smaller_than_union_size():
    benign_matrix = np.array([[0.1], [0.4]], dtype=np.float32)
    benign_index = [_chunk("benign-a", 0), _chunk("benign-b", 1)]
    attack_matrix = np.array([[0.9]], dtype=np.float32)
    attack_index = [_chunk("attack-x", 0)]

    results = attack_common.retrieve_union(
        question="q",
        benign_matrix=benign_matrix,
        benign_index=benign_index,
        attack_matrix=attack_matrix,
        attack_index=attack_index,
        k=2,
    )

    assert len(results) == 2
    assert [r.doc_id for r in results] == ["attack-x", "benign-b"]


def test_retrieve_union_clamps_k_to_union_size():
    benign_matrix = np.array([[0.3]], dtype=np.float32)
    benign_index = [_chunk("benign-a", 0)]
    attack_matrix = np.array([[0.6]], dtype=np.float32)
    attack_index = [_chunk("attack-x", 0)]

    results = attack_common.retrieve_union(
        question="q",
        benign_matrix=benign_matrix,
        benign_index=benign_index,
        attack_matrix=attack_matrix,
        attack_index=attack_index,
        k=10,
    )

    assert len(results) == 2


def test_fake_chunk_from_markdown_uses_given_doc_id(tmp_path):
    path = tmp_path / "fake.md"
    path.write_text("hello world", encoding="utf-8")
    chunk = attack_common.fake_chunk_from_markdown(path, "attack:custom")
    assert chunk == {
        "doc_id": "attack:custom",
        "page_num": 1,
        "chunk_idx": 0,
        "char_start": 0,
        "char_end": len("hello world"),
        "text": "hello world",
    }
