"""Tests for the LLM-as-judge faithfulness check (Brique 6, OWASP LLM09).

Deterministic unit tests only — the OpenRouter call is stubbed the same
way ``tests/test_ask.py::test_answer_from_chunks_assembles_instrumented_answer``
stubs ``ask._client``. No network, no model load.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from judge import JudgeParseError, _parse_judge_json, judge_faithfulness
from retrieve import RetrievalResult


def _mk_chunk(text: str = "Contenu extrait.") -> RetrievalResult:
    return RetrievalResult(
        doc_id="mfa",
        page_num=1,
        chunk_idx=0,
        char_start=0,
        char_end=len(text),
        text=text,
        score=0.5,
    )


# --- _parse_judge_json --------------------------------------------------


def test_parse_judge_json_plain():
    parsed = _parse_judge_json('{"faithful": true, "unsupported_claims": [], "reasoning": "ok"}')
    assert parsed["faithful"] is True


def test_parse_judge_json_tolerates_markdown_fence():
    raw = '```json\n{"faithful": false, "unsupported_claims": ["x"], "reasoning": "no"}\n```'
    parsed = _parse_judge_json(raw)
    assert parsed["faithful"] is False
    assert parsed["unsupported_claims"] == ["x"]


def test_parse_judge_json_tolerates_bare_fence():
    raw = '```\n{"faithful": true}\n```'
    parsed = _parse_judge_json(raw)
    assert parsed["faithful"] is True


def test_parse_judge_json_raises_on_invalid_json():
    with pytest.raises(JudgeParseError):
        _parse_judge_json("this is not json")


def test_parse_judge_json_raises_when_faithful_key_missing():
    with pytest.raises(JudgeParseError):
        _parse_judge_json('{"unsupported_claims": [], "reasoning": "oops, no verdict"}')


def test_parse_judge_json_raises_when_faithful_is_a_string_not_a_bool():
    # bool("false") == True in Python — a permissive cast would silently
    # invert this verdict. Must raise, not coerce.
    with pytest.raises(JudgeParseError):
        _parse_judge_json('{"faithful": "false", "unsupported_claims": [], "reasoning": "x"}')


def test_parse_judge_json_raises_when_unsupported_claims_is_not_a_list_of_strings():
    with pytest.raises(JudgeParseError):
        _parse_judge_json('{"faithful": false, "unsupported_claims": "not a list", "reasoning": "x"}')


def test_parse_judge_json_raises_when_reasoning_is_not_a_string():
    with pytest.raises(JudgeParseError):
        _parse_judge_json('{"faithful": true, "unsupported_claims": [], "reasoning": 42}')


def test_parse_judge_json_raises_when_faithful_true_but_claims_nonempty():
    with pytest.raises(JudgeParseError):
        _parse_judge_json(
            '{"faithful": true, "unsupported_claims": ["x"], "reasoning": "inconsistent"}'
        )


# --- judge_faithfulness ---------------------------------------------------


def _stub_client(monkeypatch, content: str, prompt_tokens: int = 10, completion_tokens: int = 5):
    fake_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )

    class _FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return fake_resp

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr("judge._client", lambda: _FakeClient())


def test_judge_faithfulness_parses_faithful_verdict(monkeypatch):
    _stub_client(
        monkeypatch,
        '{"faithful": true, "unsupported_claims": [], "reasoning": "Tout est supporté."}',
        prompt_tokens=123,
        completion_tokens=45,
    )
    verdict = judge_faithfulness(
        "Quelle est la politique de mot de passe ?",
        [_mk_chunk("16 caractères minimum.")],
        "Le mot de passe doit faire 16 caractères [1].",
        model="stub-judge",
    )
    assert verdict.faithful is True
    assert verdict.unsupported_claims == ()
    assert verdict.judge_model == "stub-judge"
    assert verdict.judge_temperature == 0.0
    assert verdict.judge_tokens_in == 123
    assert verdict.judge_tokens_out == 45


def test_judge_faithfulness_parses_unfaithful_verdict(monkeypatch):
    _stub_client(
        monkeypatch,
        '{"faithful": false, "unsupported_claims": ["renouvellement tous les 30 jours"], '
        '"reasoning": "Le contexte ne mentionne aucune durée de renouvellement."}',
    )
    verdict = judge_faithfulness(
        "Quelle est la politique de mot de passe ?",
        [_mk_chunk("16 caractères minimum.")],
        "Renouvellement tous les 30 jours [1].",
    )
    assert verdict.faithful is False
    assert verdict.unsupported_claims == ("renouvellement tous les 30 jours",)


def test_judge_faithfulness_handles_empty_context(monkeypatch):
    _stub_client(monkeypatch, '{"faithful": true, "unsupported_claims": [], "reasoning": "Refus, pas de contexte."}')
    verdict = judge_faithfulness(
        "Quelle est la capitale du Kazakhstan ?", [], "Je ne dispose pas de cette information."
    )
    assert verdict.faithful is True


def test_judge_faithfulness_raises_judge_parse_error_on_malformed_response(monkeypatch):
    _stub_client(monkeypatch, "not json at all")
    with pytest.raises(JudgeParseError):
        judge_faithfulness("Q?", [_mk_chunk()], "A.")


def test_judge_faithfulness_raises_on_string_faithful_instead_of_bool(monkeypatch):
    _stub_client(monkeypatch, '{"faithful": "false", "unsupported_claims": [], "reasoning": "x"}')
    with pytest.raises(JudgeParseError):
        judge_faithfulness("Q?", [_mk_chunk()], "A.")
