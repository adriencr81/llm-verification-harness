"""Tests for the end-to-end RAG pipeline (Brique 3)."""

from __future__ import annotations

import os

import pytest

from types import SimpleNamespace

from ask import (
    Answer,
    _answer_from_chunks,
    _build_user_message,
    _extract_citations,
    _format_context,
    ask,
    is_refusal_signal,
)
from retrieve import RetrievalResult

# Mirrors the ``_requires_artifacts`` pattern from ``test_embeddings.py`` —
# CI runs ``pytest`` without an OpenRouter secret; the 3 integration tests
# must skip cleanly instead of erroring on ``RuntimeError`` from ``_client``.
_requires_openrouter = pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="set OPENROUTER_API_KEY to run LLM integration tests",
)


def _mk_chunk(
    doc_id: str = "mfa",
    page_num: int = 1,
    chunk_idx: int = 0,
    text: str = "Contenu extrait.",
) -> RetrievalResult:
    return RetrievalResult(
        doc_id=doc_id,
        page_num=page_num,
        chunk_idx=chunk_idx,
        char_start=0,
        char_end=len(text),
        text=text,
        score=0.5,
    )


# --- Unit tests (no LLM call) -----------------------------------------------


def test_format_context_numbers_chunks_from_1_and_includes_source():
    chunks = [_mk_chunk("mfa", 3, 0, "Alpha."), _mk_chunk("pra", 12, 4, "Bravo.")]
    out = _format_context(chunks)
    assert "[1] source : mfa, page 3" in out
    assert "[2] source : pra, page 12" in out
    assert "Alpha." in out
    assert "Bravo." in out


def test_build_user_message_includes_question_and_context():
    chunks = [_mk_chunk("mfa", 3, 0, "Une phrase.")]
    msg = _build_user_message("Quoi ?", chunks)
    assert "Question : Quoi ?" in msg
    assert "Contexte" in msg
    assert "Une phrase." in msg


def test_build_user_message_handles_empty_retrieval_gracefully():
    msg = _build_user_message("Question orpheline ?", [])
    assert "aucun extrait" in msg
    assert "Question : Question orpheline ?" in msg


def test_extract_citations_parses_valid_ids_dedups_and_ignores_out_of_range():
    chunks = [_mk_chunk("a"), _mk_chunk("b"), _mk_chunk("c")]
    text = "Foo [1] bar [2] baz [1] hors-range [7]."
    citations = _extract_citations(text, chunks)
    assert [c.citation_id for c in citations] == [1, 2]
    assert citations[0].doc_id == "a"
    assert citations[1].doc_id == "b"


def test_extract_citations_empty_when_no_bracket_refs():
    chunks = [_mk_chunk("a")]
    assert _extract_citations("Réponse sans référence.", chunks) == ()


def test_answer_from_chunks_assembles_instrumented_answer(monkeypatch):
    """The shared chunks-to-Answer helper (also used by the B4 injection
    demo) must round-trip a mocked LLM response into a fully-populated
    ``Answer``: citations resolved against the passed-in chunks (not a
    re-retrieval), usage tokens carried through, ``retrieved_chunks``
    mirroring the input tuple. Guards the single code path both
    ``ask()`` and ``demo_injection.run_demo()`` share."""
    fake_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Réponse [1] et [2].")
            )
        ],
        usage=SimpleNamespace(prompt_tokens=42, completion_tokens=17),
    )

    class _FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return fake_resp

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr("ask._client", lambda: _FakeClient())

    chunks = [_mk_chunk("mfa", 3, 0, "Alpha."), _mk_chunk("pra", 12, 4, "Bravo.")]
    ans = _answer_from_chunks("Question ?", chunks, model="stub-model")

    assert ans.text == "Réponse [1] et [2]."
    assert ans.tokens_in == 42
    assert ans.tokens_out == 17
    assert ans.model == "stub-model"
    assert ans.retrieved_chunks == tuple(chunks)
    assert len(ans.citations) == 2
    assert ans.citations[0].doc_id == "mfa"
    assert ans.citations[1].doc_id == "pra"


# --- Integration tests (call the real LLM via OpenRouter) -------------------


@pytest.mark.integration
@_requires_openrouter
def test_ask_smoke_answers_typical_anssi_question():
    """Smoke: returns an ``Answer``, either cites at least one chunk or
    refuses cleanly. The retrieval on this specific question can miss the
    canonical ``mfa`` guide (observed in dev — top-4 sometimes returns
    ``secnumcloud`` bibliographic refs instead), so accept both outcomes:
    the pipeline must *never* hallucinate — it must cite or refuse."""
    answer = ask("Quelles sont les recommandations MFA de l'ANSSI ?")
    assert isinstance(answer, Answer)
    assert answer.text.strip() != ""
    assert len(answer.retrieved_chunks) == 4
    assert answer.tokens_in > 0
    assert answer.tokens_out > 0
    assert answer.latency_ms > 0
    assert len(answer.citations) >= 1 or is_refusal_signal(answer.text), (
        f"Expected at least one citation or a refusal signal, got:\n{answer.text}"
    )


@pytest.mark.integration
@_requires_openrouter
def test_ask_citations_reference_retrieved_chunks_consistently():
    answer = ask("Comment sécuriser un accès à distance ?")
    assert len(answer.citations) >= 1
    for c in answer.citations:
        idx = c.citation_id - 1
        assert 0 <= idx < len(answer.retrieved_chunks)
        src = answer.retrieved_chunks[idx]
        assert c.doc_id == src.doc_id
        assert c.page_num == src.page_num
        assert c.chunk_idx == src.chunk_idx


@pytest.mark.integration
@_requires_openrouter
def test_ask_off_topic_question_produces_refusal_and_no_hallucination():
    """Off-topic geography question: model must refuse *and* not hallucinate
    a capital. The negative geographic assertion discriminates a genuine
    refusal from a hallucinated answer that happens to contain a soft
    signal word like ``"contexte"`` or ``"aucune"``."""
    answer = ask("Quelle est la capitale du Kazakhstan ?")
    text_lower = answer.text.lower()
    assert is_refusal_signal(answer.text), (
        f"Expected a refusal signal in off-topic answer, got:\n{answer.text}"
    )
    for hallucination in ("astana", "nur-sultan", "almaty"):
        assert hallucination not in text_lower, (
            f"Off-topic answer must not name a Kazakh city, got:\n{answer.text}"
        )
