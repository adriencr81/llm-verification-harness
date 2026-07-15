"""Tests for the Brique 6 faithfulness judge (OWASP LLM09).

Deterministic only — the real ``judge()`` calls OpenRouter. Every test
here injects a stub ``chat_completion`` callable so the suite runs
without network or API key. The real judge is exercised through
``bench_runner.py`` (CLI, not CI) on the FAITH-01 YAML case, same
posture as the B3/B4 integration tests.
"""

from __future__ import annotations

import pytest

from ask import Answer, Citation
from faithfulness_judge import (
    JudgeVerdict,
    _cited_chunks,
    _parse_verdict_json,
    judge,
)
from retrieve import RetrievalResult


def _chunk(doc_id: str = "mfa", page_num: int = 1, chunk_idx: int = 0, text: str = "x") -> RetrievalResult:
    return RetrievalResult(
        doc_id=doc_id,
        page_num=page_num,
        chunk_idx=chunk_idx,
        char_start=0,
        char_end=len(text),
        text=text,
        score=0.5,
    )


def _mk_answer(
    citations: tuple[Citation, ...] = (),
    retrieved: tuple[RetrievalResult, ...] = (),
    text: str = "Une réponse.",
) -> Answer:
    return Answer(
        text=text,
        citations=citations,
        retrieved_chunks=retrieved,
        model="stub",
        temperature=0.0,
        latency_ms=0,
        tokens_in=0,
        tokens_out=0,
    )


# --- _cited_chunks -----------------------------------------------------


def test_cited_chunks_returns_chunks_matching_citation_ids_in_order():
    c1 = _chunk("mfa", 1, 0, "alpha")
    c2 = _chunk("hygiene", 2, 0, "beta")
    c3 = _chunk("admin-si", 3, 0, "gamma")
    answer = _mk_answer(
        citations=(
            Citation(citation_id=3, doc_id="admin-si", page_num=3, chunk_idx=0),
            Citation(citation_id=1, doc_id="mfa", page_num=1, chunk_idx=0),
        ),
        retrieved=(c1, c2, c3),
    )
    result = _cited_chunks(answer)
    assert result == (c3, c1)  # order = citation order


def test_cited_chunks_dedupes_same_citation_index():
    c1 = _chunk("mfa", 1, 0, "alpha")
    answer = _mk_answer(
        citations=(
            Citation(citation_id=1, doc_id="mfa", page_num=1, chunk_idx=0),
            Citation(citation_id=1, doc_id="mfa", page_num=1, chunk_idx=0),
        ),
        retrieved=(c1,),
    )
    assert _cited_chunks(answer) == (c1,)


def test_cited_chunks_empty_when_no_citations():
    c1 = _chunk()
    assert _cited_chunks(_mk_answer(retrieved=(c1,))) == ()


def test_cited_chunks_skips_out_of_range_citations():
    # ask._extract_citations validates indices — but if a future refactor
    # ever produces an out-of-range citation, _cited_chunks must not
    # IndexError. Belt-and-braces boundary.
    c1 = _chunk()
    answer = _mk_answer(
        citations=(Citation(citation_id=99, doc_id="mfa", page_num=1, chunk_idx=0),),
        retrieved=(c1,),
    )
    assert _cited_chunks(answer) == ()


# --- _parse_verdict_json -----------------------------------------------


def test_parse_verdict_json_well_formed():
    grounded, reason = _parse_verdict_json('{"grounded": true, "reason": "ok"}')
    assert grounded is True
    assert reason == "ok"


def test_parse_verdict_json_false_with_reason():
    grounded, reason = _parse_verdict_json(
        '{"grounded": false, "reason": "chiffre inventé : 42 ans"}'
    )
    assert grounded is False
    assert "42 ans" in reason


def test_parse_verdict_json_extracts_from_code_fence():
    raw = 'Voici le verdict :\n```json\n{"grounded": true, "reason": "ok"}\n```\n'
    grounded, reason = _parse_verdict_json(raw)
    assert grounded is True
    assert reason == "ok"


def test_parse_verdict_json_returns_false_on_malformed():
    grounded, reason = _parse_verdict_json("not json at all")
    assert grounded is False
    assert "not JSON-parseable" in reason


def test_parse_verdict_json_returns_false_on_missing_grounded_bool():
    grounded, reason = _parse_verdict_json('{"reason": "ok"}')
    assert grounded is False
    assert "grounded" in reason


def test_parse_verdict_json_returns_false_on_non_bool_grounded():
    # A judge that returns `"grounded": "yes"` (string, not bool) is
    # off-contract — treat as not-grounded rather than truthy-coerce.
    grounded, reason = _parse_verdict_json('{"grounded": "yes", "reason": "ok"}')
    assert grounded is False


# --- judge() -----------------------------------------------------------


def _stub_completion(response_text: str):
    def _stub(system, user, model, temperature):
        return response_text, 42, 10, 5

    return _stub


def test_judge_returns_verdict_from_chat_completion():
    c1 = _chunk(text="alpha content")
    answer = _mk_answer(
        citations=(Citation(citation_id=1, doc_id="mfa", page_num=1, chunk_idx=0),),
        retrieved=(c1,),
    )
    verdict = judge(
        answer,
        chat_completion=_stub_completion('{"grounded": true, "reason": "ok"}'),
        model="stub-judge",
        temperature=0.0,
    )
    assert isinstance(verdict, JudgeVerdict)
    assert verdict.grounded is True
    assert verdict.reason == "ok"
    assert verdict.model == "stub-judge"
    assert verdict.latency_ms == 42


def test_judge_returns_not_grounded_when_judge_says_no():
    c1 = _chunk(text="alpha content")
    answer = _mk_answer(
        citations=(Citation(citation_id=1, doc_id="mfa", page_num=1, chunk_idx=0),),
        retrieved=(c1,),
    )
    verdict = judge(
        answer,
        chat_completion=_stub_completion(
            '{"grounded": false, "reason": "chiffre inventé"}'
        ),
    )
    assert verdict.grounded is False
    assert "chiffre" in verdict.reason


def test_judge_falls_back_to_retrieved_when_no_citations_and_flags_it():
    c1 = _chunk(text="alpha")
    answer = _mk_answer(citations=(), retrieved=(c1,))
    seen: dict = {}

    def _stub(system, user, model, temperature):
        seen["user"] = user
        return '{"grounded": true, "reason": "ok"}', 0, 0, 0

    verdict = judge(answer, chat_completion=_stub)
    assert verdict.grounded is True
    assert "no citations resolved" in verdict.reason
    # The user message should still list retrieved chunks as sources.
    assert "alpha" in seen["user"]


def test_judge_returns_not_grounded_when_no_chunks_at_all():
    answer = _mk_answer(citations=(), retrieved=())
    calls = {"n": 0}

    def _stub(system, user, model, temperature):
        calls["n"] += 1
        return '{"grounded": true, "reason": "ok"}', 0, 0, 0

    verdict = judge(answer, chat_completion=_stub)
    assert verdict.grounded is False
    assert "nothing to ground" in verdict.reason
    assert calls["n"] == 0  # no LLM call when there's nothing to judge


def test_judge_forwards_answer_text_to_chat_completion():
    c1 = _chunk(text="alpha")
    answer = _mk_answer(
        citations=(Citation(citation_id=1, doc_id="mfa", page_num=1, chunk_idx=0),),
        retrieved=(c1,),
        text="La longueur minimale est 12 caractères [1].",
    )
    seen: dict = {}

    def _stub(system, user, model, temperature):
        seen["system"] = system
        seen["user"] = user
        return '{"grounded": true, "reason": "ok"}', 0, 0, 0

    judge(answer, chat_completion=_stub)
    assert "12 caractères" in seen["user"]
    assert "alpha" in seen["user"]
    assert "RÉPONSE" in seen["user"]
    assert "EXTRAITS CITÉS" in seen["user"]
    assert "juge" in seen["system"].lower()


def test_judge_raw_response_captured_for_vcd_evidence():
    c1 = _chunk(text="alpha")
    answer = _mk_answer(
        citations=(Citation(citation_id=1, doc_id="mfa", page_num=1, chunk_idx=0),),
        retrieved=(c1,),
    )
    raw = '{"grounded": true, "reason": "ok — chiffres présents dans [1]"}'
    verdict = judge(answer, chat_completion=_stub_completion(raw))
    assert verdict.raw_response == raw
