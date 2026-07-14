"""Tests for the Brique 5 bench runner — schema validation and check logic.

Deterministic only: every target (``ask``, ``injection_demo``) hits a real
LLM and/or loads BGE-M3, so this file never calls ``run_case``/``run_cases``
against the real targets — instead it monkeypatches ``bench_runner.TARGETS``
or (for the normalisation layer itself) the underlying ``ask.ask`` /
``demo_injection.run_demo`` functions. Checks are exercised directly
against a stubbed :class:`bench_runner.CaseContext`; schema loading is
exercised against temp files plus the real committed ``bench/cases/*.yaml``
(regression guard: every case that ships must still satisfy the schema,
and every requirement id it cites must exist in docs/REQUIREMENTS.md).
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import bench_runner
from ask import Answer, Citation
from bench_runner import (
    Case,
    CaseContext,
    CaseResult,
    CaseSchemaError,
    CheckSpec,
    load_case,
    load_cases,
    run_case,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_case(tmp_path: Path, name: str, data: dict) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _valid_case_dict(**overrides) -> dict:
    base = {
        "id": "TEST-01",
        "requirement": "REQ-TEST-01",
        "title": "A test case",
        "target": "ask",
        "input": {"question": "Une question ?"},
        "checks": [{"type": "has_citation"}],
    }
    base.update(overrides)
    return base


# --- Schema validation -------------------------------------------------


def test_load_case_valid_file_round_trips_fields(tmp_path: Path):
    path = _write_case(tmp_path, "case.yaml", _valid_case_dict(description="  desc  "))
    case = load_case(path)
    assert case.id == "TEST-01"
    assert case.requirement == "REQ-TEST-01"
    assert case.target == "ask"
    assert case.input == {"question": "Une question ?"}
    assert case.checks == (CheckSpec(type="has_citation", params={}),)
    assert case.description == "desc"
    assert case.expected == "PASS"
    assert case.source_path == path


def test_load_case_missing_required_field_raises(tmp_path: Path):
    data = _valid_case_dict()
    del data["title"]
    path = _write_case(tmp_path, "case.yaml", data)
    with pytest.raises(CaseSchemaError, match="missing required field"):
        load_case(path)


def test_load_case_unknown_target_raises(tmp_path: Path):
    path = _write_case(tmp_path, "case.yaml", _valid_case_dict(target="not-a-target"))
    with pytest.raises(CaseSchemaError, match="unknown target"):
        load_case(path)


def test_load_case_empty_checks_raises(tmp_path: Path):
    path = _write_case(tmp_path, "case.yaml", _valid_case_dict(checks=[]))
    with pytest.raises(CaseSchemaError, match="non-empty list"):
        load_case(path)


def test_load_case_unknown_check_type_raises(tmp_path: Path):
    path = _write_case(
        tmp_path, "case.yaml", _valid_case_dict(checks=[{"type": "not-a-check"}])
    )
    with pytest.raises(CaseSchemaError, match="unknown check type"):
        load_case(path)


def test_load_case_check_missing_type_raises(tmp_path: Path):
    path = _write_case(tmp_path, "case.yaml", _valid_case_dict(checks=[{}]))
    with pytest.raises(CaseSchemaError, match="missing 'type'"):
        load_case(path)


def test_load_case_top_level_not_a_mapping_raises(tmp_path: Path):
    path = tmp_path / "case.yaml"
    path.write_text(yaml.safe_dump(["not", "a", "mapping"]), encoding="utf-8")
    with pytest.raises(CaseSchemaError, match="must be a mapping"):
        load_case(path)


def test_load_case_check_params_default_to_empty_dict(tmp_path: Path):
    path = _write_case(
        tmp_path,
        "case.yaml",
        _valid_case_dict(checks=[{"type": "refusal_signal", "params": None}]),
    )
    case = load_case(path)
    assert case.checks[0].params == {}


def test_load_case_missing_required_check_param_raises(tmp_path: Path):
    # no_forbidden_terms requires params.terms — a case missing it must be
    # refused at load time, not discovered as a KeyError mid-run.
    path = _write_case(
        tmp_path, "case.yaml", _valid_case_dict(checks=[{"type": "no_forbidden_terms"}])
    )
    with pytest.raises(CaseSchemaError, match="requires param"):
        load_case(path)


def test_load_case_no_forbidden_terms_with_params_loads(tmp_path: Path):
    path = _write_case(
        tmp_path,
        "case.yaml",
        _valid_case_dict(
            checks=[{"type": "no_forbidden_terms", "params": {"terms": ["x"]}}]
        ),
    )
    case = load_case(path)
    assert case.checks[0].params == {"terms": ["x"]}


def test_load_case_missing_required_target_input_raises(tmp_path: Path):
    # target ask requires input.question.
    data = _valid_case_dict()
    data["input"] = {}
    path = _write_case(tmp_path, "case.yaml", data)
    with pytest.raises(CaseSchemaError, match="requires input"):
        load_case(path)


def test_load_case_faithful_to_context_rejects_incompatible_target(tmp_path: Path):
    # faithful_to_context needs retrieved_chunks on ctx.raw, which only
    # the ``ask`` target's Answer carries — refused at load time rather
    # than silently judging an empty context and burning a real LLM call.
    data = _valid_case_dict(
        target="injection_demo", input={}, checks=[{"type": "faithful_to_context"}]
    )
    path = _write_case(tmp_path, "case.yaml", data)
    with pytest.raises(CaseSchemaError, match="only compatible with target"):
        load_case(path)


def test_load_case_faithful_to_context_accepts_ask_target(tmp_path: Path):
    data = _valid_case_dict(target="ask", checks=[{"type": "faithful_to_context"}])
    path = _write_case(tmp_path, "case.yaml", data)
    case = load_case(path)
    assert case.checks[0].type == "faithful_to_context"


def test_load_case_injection_demo_target_has_no_required_input(tmp_path: Path):
    # injection_demo's question has a default in demo_injection.py — an
    # empty input dict must still load.
    data = _valid_case_dict(
        target="injection_demo", input={}, checks=[{"type": "fake_doc_in_top_k"}]
    )
    path = _write_case(tmp_path, "case.yaml", data)
    case = load_case(path)
    assert case.input == {}


def test_load_case_expected_defaults_to_pass(tmp_path: Path):
    path = _write_case(tmp_path, "case.yaml", _valid_case_dict())
    assert load_case(path).expected == "PASS"


def test_load_case_expected_fail_round_trips(tmp_path: Path):
    path = _write_case(tmp_path, "case.yaml", _valid_case_dict(expected="FAIL"))
    assert load_case(path).expected == "FAIL"


def test_load_case_invalid_expected_value_raises(tmp_path: Path):
    path = _write_case(tmp_path, "case.yaml", _valid_case_dict(expected="MAYBE"))
    with pytest.raises(CaseSchemaError, match="'expected'"):
        load_case(path)


def test_load_cases_rejects_duplicate_ids(tmp_path: Path):
    _write_case(tmp_path, "a.yaml", _valid_case_dict())
    _write_case(tmp_path, "b.yaml", _valid_case_dict())
    with pytest.raises(CaseSchemaError, match="duplicate case id"):
        load_cases(tmp_path)


def test_load_cases_sorted_by_id(tmp_path: Path):
    _write_case(tmp_path, "a.yaml", _valid_case_dict(id="ZZZ"))
    _write_case(tmp_path, "b.yaml", _valid_case_dict(id="AAA"))
    cases = load_cases(tmp_path)
    assert [c.id for c in cases] == ["AAA", "ZZZ"]


def test_committed_bench_cases_all_satisfy_the_schema():
    """Regression guard: every case shipped in bench/cases/ must load —
    a hand-edited YAML that drifts from the schema fails here, not at
    demo time in front of a reviewer."""
    cases_dir = REPO_ROOT / "bench" / "cases"
    cases = load_cases(cases_dir)
    assert len(cases) >= 4
    for case in cases:
        assert case.requirement.startswith("REQ-")
        assert case.target in bench_runner.TARGETS
        assert case.expected in ("PASS", "FAIL")


def test_committed_bench_cases_requirements_exist_in_registry():
    """Bidirectional traceability: every ``requirement`` cited by a
    committed case must have a ``### `REQ-...``` heading in
    docs/REQUIREMENTS.md — the frozen registry is only as trustworthy
    as this cross-check. An orphaned case (citing a REQ-* that was
    renamed or never documented) fails here rather than silently
    resting on human diligence."""
    registry_text = (REPO_ROOT / "docs" / "REQUIREMENTS.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^### `(REQ-[A-Z]+-\d+)`", registry_text, re.MULTILINE))

    cases = load_cases(REPO_ROOT / "bench" / "cases")
    cited = {case.requirement for case in cases}
    orphans = cited - documented
    assert not orphans, f"case(s) cite undocumented requirement(s): {orphans}"


# --- Checks --------------------------------------------------------------


def _ctx(
    text: str = "",
    citations: tuple = (),
    extra: dict | None = None,
    raw=None,
    question: str = "",
    model: str = "stub-model",
    temperature: float = 0.0,
    latency_ms: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> CaseContext:
    return CaseContext(
        text=text,
        citations=citations,
        extra=extra or {},
        raw=raw,
        question=question,
        model=model,
        temperature=temperature,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


def test_check_refusal_signal_true_and_false():
    assert bench_runner._check_refusal_signal(_ctx("Le contexte ne permet pas de répondre."), {}).passed
    assert not bench_runner._check_refusal_signal(_ctx("Réponse factuelle."), {}).passed


def test_check_no_forbidden_terms():
    params = {"terms": ["astana", "almaty"]}
    ok = bench_runner._check_no_forbidden_terms(_ctx("Aucune information."), params)
    assert ok.passed
    bad = bench_runner._check_no_forbidden_terms(_ctx("La capitale est Astana."), params)
    assert not bad.passed
    assert "astana" in bad.detail.lower()


def test_check_has_citation():
    c = Citation(citation_id=1, doc_id="mfa", page_num=1, chunk_idx=0)
    assert bench_runner._check_has_citation(_ctx(citations=(c,)), {}).passed
    assert not bench_runner._check_has_citation(_ctx(citations=()), {}).passed


class _FakeChunk:
    def __init__(self, doc_id, page_num, chunk_idx):
        self.doc_id = doc_id
        self.page_num = page_num
        self.chunk_idx = chunk_idx


class _FakeAnswer:
    def __init__(self, retrieved_chunks):
        self.retrieved_chunks = retrieved_chunks


def test_check_citations_consistent_true_when_aligned():
    chunk = _FakeChunk("mfa", 3, 0)
    citation = Citation(citation_id=1, doc_id="mfa", page_num=3, chunk_idx=0)
    ctx = _ctx(citations=(citation,), raw=_FakeAnswer((chunk,)))
    assert bench_runner._check_citations_consistent(ctx, {}).passed


def test_check_citations_consistent_false_when_out_of_range():
    citation = Citation(citation_id=5, doc_id="mfa", page_num=3, chunk_idx=0)
    ctx = _ctx(citations=(citation,), raw=_FakeAnswer(()))
    result = bench_runner._check_citations_consistent(ctx, {})
    assert not result.passed
    assert "out of range" in result.detail


def test_check_citations_consistent_false_when_mismatched():
    chunk = _FakeChunk("mfa", 3, 0)
    citation = Citation(citation_id=1, doc_id="pra", page_num=3, chunk_idx=0)
    ctx = _ctx(citations=(citation,), raw=_FakeAnswer((chunk,)))
    result = bench_runner._check_citations_consistent(ctx, {})
    assert not result.passed


def test_check_fake_doc_in_top_k():
    assert bench_runner._check_fake_doc_in_top_k(_ctx(extra={"fake_doc_in_top_k": True}), {}).passed
    assert not bench_runner._check_fake_doc_in_top_k(_ctx(extra={"fake_doc_in_top_k": False}), {}).passed


def test_check_payload_absent_and_present_are_opposite():
    ctx_leaked = _ctx(extra={"payload_found": True})
    ctx_clean = _ctx(extra={"payload_found": False})
    assert bench_runner._check_payload_absent(ctx_clean, {}).passed
    assert not bench_runner._check_payload_absent(ctx_leaked, {}).passed
    assert bench_runner._check_payload_present(ctx_leaked, {}).passed
    assert not bench_runner._check_payload_present(ctx_clean, {}).passed


def test_check_fake_doc_not_cited():
    cited = _ctx(extra={"fake_doc_cited_as_source": True})
    not_cited = _ctx(extra={"fake_doc_cited_as_source": False})
    assert bench_runner._check_fake_doc_not_cited(not_cited, {}).passed
    assert not bench_runner._check_fake_doc_not_cited(cited, {}).passed


def test_check_leak_absent():
    leaked = _ctx(extra={"leak_found": True, "leaked_canaries": ("frag",)})
    clean = _ctx(extra={"leak_found": False})
    assert bench_runner._check_leak_absent(clean, {}).passed
    result = bench_runner._check_leak_absent(leaked, {})
    assert not result.passed
    assert "frag" in result.detail


def test_check_faithful_to_context_calls_judge_with_question_and_chunks(monkeypatch):
    seen = {}

    def _fake_judge_faithfulness(question, context_chunks, answer_text, model, temperature):
        seen["question"] = question
        seen["context_chunks"] = context_chunks
        seen["answer_text"] = answer_text
        seen["model"] = model
        seen["temperature"] = temperature
        return SimpleNamespace(faithful=True, unsupported_claims=(), reasoning="ok")

    monkeypatch.setattr(bench_runner.judge, "judge_faithfulness", _fake_judge_faithfulness)

    chunk = _FakeChunk("mfa", 1, 0)
    ctx = _ctx(
        text="Réponse [1].",
        question="Une question ?",
        raw=_FakeAnswer((chunk,)),
    )
    result = bench_runner._check_faithful_to_context(ctx, {})

    assert result.passed
    assert seen["question"] == "Une question ?"
    assert seen["context_chunks"] == [chunk]
    assert seen["answer_text"] == "Réponse [1]."
    assert seen["model"] == bench_runner.judge.JUDGE_MODEL
    assert seen["temperature"] == bench_runner.judge.JUDGE_TEMPERATURE


def test_check_faithful_to_context_uses_judge_model_and_temperature_params(monkeypatch):
    seen = {}

    def _fake_judge_faithfulness(question, context_chunks, answer_text, model, temperature):
        seen["model"] = model
        seen["temperature"] = temperature
        return SimpleNamespace(faithful=True, unsupported_claims=(), reasoning="")

    monkeypatch.setattr(bench_runner.judge, "judge_faithfulness", _fake_judge_faithfulness)
    ctx = _ctx(raw=_FakeAnswer(()))
    bench_runner._check_faithful_to_context(
        ctx, {"judge_model": "custom-judge", "judge_temperature": 0.5}
    )
    assert seen["model"] == "custom-judge"
    assert seen["temperature"] == 0.5


def test_check_faithful_to_context_fails_and_details_unsupported_claims(monkeypatch):
    monkeypatch.setattr(
        bench_runner.judge,
        "judge_faithfulness",
        lambda question, context_chunks, answer_text, model, temperature: SimpleNamespace(
            faithful=False, unsupported_claims=("claim X",), reasoning="pas dans le contexte"
        ),
    )
    ctx = _ctx(raw=_FakeAnswer(()))
    result = bench_runner._check_faithful_to_context(ctx, {})
    assert not result.passed
    assert "claim X" in result.detail
    assert "pas dans le contexte" in result.detail


# --- Targets: CaseContext normalisation -------------------------------


def _fake_answer(**overrides) -> Answer:
    base = dict(
        text="Réponse [1].",
        citations=(Citation(citation_id=1, doc_id="mfa", page_num=1, chunk_idx=0),),
        retrieved_chunks=(),
        model="anthropic/claude-haiku-4-5",
        temperature=0.0,
        latency_ms=123,
        tokens_in=10,
        tokens_out=5,
    )
    base.update(overrides)
    return Answer(**base)


def test_target_ask_maps_answer_fields_onto_context(monkeypatch):
    answer = _fake_answer()
    monkeypatch.setattr(bench_runner.ask, "ask", lambda question, **kw: answer)

    ctx = bench_runner._target_ask({"question": "Une question ?"})

    assert ctx.text == answer.text
    assert ctx.citations == answer.citations
    assert ctx.extra == {}
    assert ctx.raw is answer
    assert ctx.question == "Une question ?"
    assert ctx.model == answer.model
    assert ctx.temperature == answer.temperature
    assert ctx.latency_ms == answer.latency_ms
    assert ctx.tokens_in == answer.tokens_in
    assert ctx.tokens_out == answer.tokens_out


def test_target_ask_forwards_extra_params_and_strips_question(monkeypatch):
    seen = {}

    def _fake_ask(question, **kwargs):
        seen["question"] = question
        seen["kwargs"] = kwargs
        return _fake_answer()

    monkeypatch.setattr(bench_runner.ask, "ask", _fake_ask)
    bench_runner._target_ask({"question": "Q ?", "k": 2, "temperature": 0.5})

    assert seen["question"] == "Q ?"
    assert seen["kwargs"] == {"k": 2, "temperature": 0.5}


def test_target_injection_demo_maps_report_fields_onto_context(monkeypatch):
    answer = _fake_answer(text="PWNED-7Q2")
    fake_report = SimpleNamespace(
        question="Q ?",
        answer=answer,
        fake_doc_in_top_k=True,
        payload_found=True,
        fake_doc_cited_as_source=True,
        verdict="VULNERABLE — ...",
    )
    monkeypatch.setattr(bench_runner.demo_injection, "run_demo", lambda **kw: fake_report)

    ctx = bench_runner._target_injection_demo({"question": "Q ?"})

    assert ctx.text == "PWNED-7Q2"
    assert ctx.citations == answer.citations
    assert ctx.extra == {
        "fake_doc_in_top_k": True,
        "payload_found": True,
        "fake_doc_cited_as_source": True,
        "verdict": "VULNERABLE — ...",
    }
    assert ctx.raw is fake_report
    assert ctx.question == "Q ?"
    assert ctx.model == answer.model
    assert ctx.latency_ms == answer.latency_ms
    assert ctx.tokens_in == answer.tokens_in
    assert ctx.tokens_out == answer.tokens_out


def test_target_injection_demo_forwards_params_as_kwargs(monkeypatch):
    seen = {}

    def _fake_run_demo(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            question=kwargs.get("question", ""),
            answer=_fake_answer(),
            fake_doc_in_top_k=False,
            payload_found=False,
            fake_doc_cited_as_source=False,
            verdict="DEMO INVALID",
        )

    monkeypatch.setattr(bench_runner.demo_injection, "run_demo", _fake_run_demo)
    bench_runner._target_injection_demo({"question": "Q ?", "k": 6})

    assert seen == {"question": "Q ?", "k": 6}


def test_target_injection_demo_with_no_params_uses_defaults(monkeypatch):
    seen = {}

    def _fake_run_demo(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            question=kwargs.get("question", ""),
            answer=_fake_answer(),
            fake_doc_in_top_k=False,
            payload_found=False,
            fake_doc_cited_as_source=False,
            verdict="DEMO INVALID",
        )

    monkeypatch.setattr(bench_runner.demo_injection, "run_demo", _fake_run_demo)
    bench_runner._target_injection_demo({})

    assert seen == {}


# --- run_case --------------------------------------------------------------


def _mk_case(**overrides) -> Case:
    base = dict(
        id="TEST-01",
        requirement="REQ-TEST-01",
        title="stub case",
        description="",
        target="ask",
        input={},
        checks=(CheckSpec(type="has_citation", params={}),),
        expected="PASS",
        source_path=Path("stub.yaml"),
    )
    base.update(overrides)
    return Case(**base)


def test_run_case_all_checks_pass_status_pass(monkeypatch):
    monkeypatch.setitem(
        bench_runner.TARGETS, "ask", lambda params: _ctx(citations=(object(),))
    )
    case = _mk_case()
    result = run_case(case)
    assert isinstance(result, CaseResult)
    assert result.error is None
    assert result.status == "PASS"
    assert result.passed


def test_run_case_check_fails_expected_pass_is_regression(monkeypatch):
    monkeypatch.setitem(bench_runner.TARGETS, "ask", lambda params: _ctx(citations=()))
    case = _mk_case(expected="PASS")
    result = run_case(case)
    assert result.error is None
    assert result.status == "REGRESSION"
    assert not result.passed
    assert not result.check_results[0].passed


def test_run_case_check_fails_expected_fail_is_tracked(monkeypatch):
    monkeypatch.setitem(bench_runner.TARGETS, "ask", lambda params: _ctx(citations=()))
    case = _mk_case(expected="FAIL")
    result = run_case(case)
    assert result.status == "TRACKED-FAIL"
    assert result.passed  # tracked, known — not a bench failure


def test_run_case_check_passes_expected_fail_is_unexpected_pass(monkeypatch):
    monkeypatch.setitem(
        bench_runner.TARGETS, "ask", lambda params: _ctx(citations=(object(),))
    )
    case = _mk_case(expected="FAIL")
    result = run_case(case)
    assert result.status == "UNEXPECTED-PASS"
    assert not result.passed  # tracked vulnerability apparently gone — flag it


def test_run_case_target_raises_sets_error_and_fails(monkeypatch):
    def _boom(params):
        raise RuntimeError("simulated network failure")

    monkeypatch.setitem(bench_runner.TARGETS, "ask", _boom)
    case = _mk_case()
    result = run_case(case)
    assert result.status == "ERROR"
    assert not result.passed
    assert result.check_results == ()
    assert "simulated network failure" in result.error
    assert result.timestamp  # captured even on error


def test_run_case_check_raises_sets_error_and_preserves_ctx_provenance(monkeypatch):
    """Since Brique 6, a check (``faithful_to_context``) can itself hit
    the network via the judge. A check-time exception must surface as
    an ``ERROR`` CaseResult — not crash ``run_cases`` — while still
    carrying the already-succeeded target's provenance (the LLM call
    that produced the answer did work; only the judge failed)."""
    monkeypatch.setitem(
        bench_runner.TARGETS,
        "ask",
        lambda params: _ctx(
            text="Réponse.",
            model="anthropic/claude-haiku-4-5",
            latency_ms=100,
            tokens_in=5,
            tokens_out=3,
        ),
    )

    def _boom_check(ctx, params):
        raise RuntimeError("judge network failure")

    monkeypatch.setitem(bench_runner.CHECKS, "has_citation", _boom_check)
    case = _mk_case()
    result = run_case(case)

    assert result.status == "ERROR"
    assert not result.passed
    assert result.check_results == ()
    assert "judge network failure" in result.error
    assert result.text == "Réponse."
    assert result.model == "anthropic/claude-haiku-4-5"
    assert result.tokens_in == 5
    assert result.timestamp


def test_run_case_captures_run_provenance(monkeypatch):
    monkeypatch.setitem(
        bench_runner.TARGETS,
        "ask",
        lambda params: _ctx(
            text="Réponse.",
            citations=(object(),),
            model="anthropic/claude-haiku-4-5",
            temperature=0.0,
            latency_ms=456,
            tokens_in=12,
            tokens_out=34,
        ),
    )
    case = _mk_case()
    result = run_case(case)
    assert result.text == "Réponse."
    assert result.model == "anthropic/claude-haiku-4-5"
    assert result.temperature == 0.0
    assert result.latency_ms == 456
    assert result.tokens_in == 12
    assert result.tokens_out == 34
    assert result.timestamp  # non-empty ISO8601 UTC string
