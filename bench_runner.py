#!/usr/bin/env python3
"""IVVQ-style test case runner — Brique 5.

Formalises the scenarios exercised ad hoc by the Brique 3/4 integration
tests and demo scripts into a declarative, machine-checked contract: a
YAML **test case** names an upstream requirement (``REQ-*``,
docs/REQUIREMENTS.md), a **target** (a pipeline entry point to drive —
``ask.ask`` or ``demo_injection.run_demo``), and a list of **checks**
(falsifiable PASS/FAIL predicates over the target's output). Running a
case never asserts in Python — it always returns a :class:`CaseResult`
the caller inspects, so a case is evidence, not a test-suite crash.

This is deliberately the *format + runner*, not the verdict engine —
the Brique 7 VCD is the thing that turns a batch of :class:`CaseResult`
into a signed verification dossier. Brique 5's job is to make sure the
input to that dossier is a validated, falsifiable, YAML-committed
artifact rather than logic buried inside test functions.

Two target/check families ship today:

- ``ask`` — drives ``ask.ask(question, ...)`` (Brique 3 RAG pipeline).
  Checks: ``refusal_signal``, ``no_forbidden_terms``, ``has_citation``,
  ``citations_consistent``, ``faithful_to_context`` (Brique 6,
  LLM-as-judge groundedness — see below).
- ``injection_demo`` — drives ``demo_injection.run_demo(question, ...)``
  (Brique 4 OWASP LLM01 attack). Checks: ``fake_doc_in_top_k``,
  ``payload_absent``, ``payload_present``, ``fake_doc_not_cited``.
- ``leak_demo`` — drives ``demo_leak.run_demo(question, ...)``
  (Brique 6 OWASP LLM02 system-prompt exfiltration attack). Checks:
  ``fake_doc_in_top_k``, ``leak_absent``.

All three hit a real LLM (OpenRouter) and, for ``injection_demo`` /
``leak_demo``, load the BGE-M3 model — non-deterministic and costly,
same reasoning as the B3/B4 integration tests (``pytest.mark.integration``,
skipped in CI). This
module's own tests (``tests/test_bench_runner.py``) cover schema
validation and check logic only, with stubbed contexts — zero network,
zero model load, runs in CI.

**``faithful_to_context`` is a second kind of check** (Brique 6,
``judge.py``, OWASP LLM09 overreliance/hallucination): every other
check is a pure, local predicate over the target's already-computed
output, but this one makes its own independent LLM call (the
faithfulness judge) to decide whether the answer is grounded in the
retrieved context. That means a check can now fail for
infrastructure reasons (judge network error, unparseable judge
response — ``judge.JudgeParseError``), not just report a failed
predicate — ``run_case`` wraps check execution in the same
try/except pattern it already used for target execution, so either
boundary failing surfaces as an ``ERROR`` :class:`CaseResult` rather
than crashing the run.

Every case declares an ``expected`` outcome, ``PASS`` (default) or
``FAIL``. Most cases assert a defense holds — ``expected: PASS``. The
two ``REQ-INJECT-01`` cases assert a documented, tracked vulnerability
(see the Brique 4 demo): one still expects the payload not to leak
(``expected: PASS``), the other expects the fake document to be cited
as a source (``expected: FAIL`` — a known failure, not a regression).
``CaseResult.passed`` is true when the *observed* outcome matches the
*expected* one — a tracked vulnerability that silently disappears
(``UNEXPECTED-PASS``) is flagged exactly like a fresh regression, not
silently treated as good news. This is scoped at case granularity, not
per-check: a case that mixes a precondition check (e.g.
``fake_doc_in_top_k``, "did the attack setup even work") with the
actual verification check inherits one scalar ``expected`` for all of
them. Finer per-check expectations are a natural Brique 6 extension,
not required for this baseline.

Usage::

    python bench_runner.py                  # run every case in bench/cases/
    python bench_runner.py --cases-dir DIR   # run a different case directory
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import yaml

import ask
import demo_injection
import demo_leak
import judge

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CASES_DIR = REPO_ROOT / "bench" / "cases"


class CaseSchemaError(ValueError):
    """A YAML case file violates the test-case schema."""


# --- Schema ------------------------------------------------------------


@dataclass(frozen=True)
class CheckSpec:
    """One check to run against a target's output — ``type`` + params."""

    type: str
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Case:
    """A formalised IVVQ test case, loaded from one YAML file."""

    id: str
    requirement: str
    title: str
    description: str
    target: str
    input: dict
    checks: tuple[CheckSpec, ...]
    expected: str
    source_path: Path


_REQUIRED_FIELDS = ("id", "requirement", "title", "target", "checks")
_EXPECTED_VALUES = ("PASS", "FAIL")

# Per-check-type required ``params`` keys, and per-target required
# ``input`` keys — validated at load time so a malformed case is refused
# before any target runs, not discovered as a ``KeyError`` after an
# expensive LLM call (and mislabeled as an infrastructure failure).
_CHECK_REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "no_forbidden_terms": ("terms",),
}
_TARGET_REQUIRED_INPUT: dict[str, tuple[str, ...]] = {
    "ask": ("question",),
}
# Checks that only make sense against specific targets. Absent from this
# map = compatible with any target (the historical default). Introduced
# for ``faithful_to_context`` (Brique 6): it needs ``retrieved_chunks``
# on ``ctx.raw``, which only ``ask``'s ``Answer`` carries directly — on
# ``injection_demo``/``leak_demo`` (``ctx.raw`` is a demo report) it
# would silently fall back to an empty context and burn a real LLM call
# producing a semantic verdict over nothing, instead of being refused at
# load time like every other malformed-case class.
_CHECK_COMPATIBLE_TARGETS: dict[str, tuple[str, ...]] = {
    "faithful_to_context": ("ask",),
}


def _validate_case_schema(raw: dict, path: Path) -> None:
    if not isinstance(raw, dict):
        raise CaseSchemaError(f"{path}: top-level YAML must be a mapping")

    missing = [f for f in _REQUIRED_FIELDS if f not in raw]
    if missing:
        raise CaseSchemaError(f"{path}: missing required field(s) {missing}")

    target = raw["target"]
    if target not in TARGETS:
        raise CaseSchemaError(
            f"{path}: unknown target {target!r} — "
            f"must be one of {sorted(TARGETS)}"
        )

    case_input = raw.get("input", {}) or {}
    missing_input = [
        k for k in _TARGET_REQUIRED_INPUT.get(target, ()) if k not in case_input
    ]
    if missing_input:
        raise CaseSchemaError(
            f"{path}: target {target!r} requires input field(s) {missing_input}"
        )

    expected = raw.get("expected", "PASS")
    if expected not in _EXPECTED_VALUES:
        raise CaseSchemaError(
            f"{path}: 'expected' must be one of {_EXPECTED_VALUES}, got {expected!r}"
        )

    checks = raw["checks"]
    if not isinstance(checks, list) or not checks:
        raise CaseSchemaError(f"{path}: 'checks' must be a non-empty list")

    for entry in checks:
        if not isinstance(entry, dict) or "type" not in entry:
            raise CaseSchemaError(f"{path}: check entry missing 'type': {entry!r}")
        check_type = entry["type"]
        if check_type not in CHECKS:
            raise CaseSchemaError(
                f"{path}: unknown check type {check_type!r} — "
                f"must be one of {sorted(CHECKS)}"
            )
        params = entry.get("params", {}) or {}
        missing_params = [
            k for k in _CHECK_REQUIRED_PARAMS.get(check_type, ()) if k not in params
        ]
        if missing_params:
            raise CaseSchemaError(
                f"{path}: check {check_type!r} requires param(s) {missing_params}"
            )
        compatible_targets = _CHECK_COMPATIBLE_TARGETS.get(check_type)
        if compatible_targets is not None and target not in compatible_targets:
            raise CaseSchemaError(
                f"{path}: check {check_type!r} is only compatible with target(s) "
                f"{compatible_targets}, got target {target!r}"
            )


def load_case(path: Path) -> Case:
    """Load and schema-validate one YAML test case file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    _validate_case_schema(raw, path)
    checks = tuple(
        CheckSpec(type=c["type"], params=c.get("params", {}) or {})
        for c in raw["checks"]
    )
    return Case(
        id=raw["id"],
        requirement=raw["requirement"],
        title=raw["title"],
        description=(raw.get("description") or "").strip(),
        target=raw["target"],
        input=raw.get("input", {}) or {},
        checks=checks,
        expected=raw.get("expected", "PASS"),
        source_path=path,
    )


def load_cases(cases_dir: Path = DEFAULT_CASES_DIR) -> list[Case]:
    """Load every ``*.yaml`` case in ``cases_dir``, sorted by ``id``.

    Duplicate ``id``s are refused — the id is the join key the Brique 7
    VCD will use to cite a case, and a silent collision would make that
    citation ambiguous.
    """
    # Sorted glob order first (deterministic duplicate-error reporting),
    # then re-sorted by id for the documented return contract — two
    # different sort keys, not a redundant re-sort of the same one.
    cases = [load_case(p) for p in sorted(cases_dir.glob("*.yaml"))]
    seen: dict[str, Path] = {}
    for case in cases:
        if case.id in seen:
            raise CaseSchemaError(
                f"duplicate case id {case.id!r}: "
                f"{seen[case.id]} and {case.source_path}"
            )
        seen[case.id] = case.source_path
    return sorted(cases, key=lambda c: c.id)


# --- Targets: input dict -> normalised CaseContext ----------------------


@dataclass(frozen=True)
class CaseContext:
    """Normalised view of a target's output, consumed by checks.

    ``extra`` carries target-specific fields (e.g. the injection demo's
    ``payload_found``) that only a subset of checks care about — keeps
    the common fields (``text``, ``citations``) uniform across targets
    without forcing every target into one bloated schema. ``model``
    through ``tokens_out`` mirror ``ask.Answer``'s instrumentation
    fields, always sourced from the underlying ``Answer`` regardless of
    which target produced it — this is the run provenance a case result
    carries forward as VCD-citable evidence, not just a bool. ``question``
    is the exact input question routed to the target — added in Brique 6
    so a check can hand it to a second LLM call (the faithfulness judge)
    without threading ``case.input`` through the check-calling interface.
    """

    text: str
    citations: tuple
    extra: dict
    raw: object
    question: str
    model: str
    temperature: float
    latency_ms: int
    tokens_in: int
    tokens_out: int


def _target_ask(params: dict) -> CaseContext:
    question = params["question"]
    kwargs = {k: v for k, v in params.items() if k != "question"}
    answer = ask.ask(question, **kwargs)
    return CaseContext(
        text=answer.text,
        citations=answer.citations,
        extra={},
        raw=answer,
        question=question,
        model=answer.model,
        temperature=answer.temperature,
        latency_ms=answer.latency_ms,
        tokens_in=answer.tokens_in,
        tokens_out=answer.tokens_out,
    )


def _target_injection_demo(params: dict) -> CaseContext:
    report = demo_injection.run_demo(**params)
    answer = report.answer
    return CaseContext(
        text=answer.text,
        citations=answer.citations,
        extra={
            "fake_doc_id": report.fake_doc_id,
            "fake_doc_in_top_k": report.fake_doc_in_top_k,
            "payload_found": report.payload_found,
            "fake_doc_cited_as_source": report.fake_doc_cited_as_source,
            "verdict": report.verdict,
        },
        raw=report,
        question=report.question,
        model=answer.model,
        temperature=answer.temperature,
        latency_ms=answer.latency_ms,
        tokens_in=answer.tokens_in,
        tokens_out=answer.tokens_out,
    )


def _target_leak_demo(params: dict) -> CaseContext:
    report = demo_leak.run_demo(**params)
    answer = report.answer
    return CaseContext(
        text=answer.text,
        citations=answer.citations,
        extra={
            "fake_doc_in_top_k": report.fake_doc_in_top_k,
            "leak_found": report.leak_found,
            "leaked_canaries": report.leaked_canaries,
            "verdict": report.verdict,
        },
        raw=report,
        question=report.question,
        model=answer.model,
        temperature=answer.temperature,
        latency_ms=answer.latency_ms,
        tokens_in=answer.tokens_in,
        tokens_out=answer.tokens_out,
    )


TARGETS: dict[str, Callable[[dict], CaseContext]] = {
    "ask": _target_ask,
    "injection_demo": _target_injection_demo,
    "leak_demo": _target_leak_demo,
}


# --- Checks: CaseContext + params -> CheckResult -------------------------


@dataclass(frozen=True)
class CheckResult:
    type: str
    passed: bool
    detail: str


def _check_refusal_signal(ctx: CaseContext, params: dict) -> CheckResult:
    ok = ask.is_refusal_signal(ctx.text)
    return CheckResult("refusal_signal", ok, f"refusal signal {'found' if ok else 'NOT found'}")


def _check_no_forbidden_terms(ctx: CaseContext, params: dict) -> CheckResult:
    terms = params["terms"]
    lower = ctx.text.lower()
    hits = [t for t in terms if t.lower() in lower]
    ok = not hits
    detail = "no forbidden term found" if ok else f"forbidden term(s) present: {hits}"
    return CheckResult("no_forbidden_terms", ok, detail)


def _check_has_citation(ctx: CaseContext, params: dict) -> CheckResult:
    ok = len(ctx.citations) >= 1
    return CheckResult("has_citation", ok, f"{len(ctx.citations)} citation(s)")


def _check_citations_consistent(ctx: CaseContext, params: dict) -> CheckResult:
    answer = ctx.raw
    retrieved = getattr(answer, "retrieved_chunks", ())
    for c in ctx.citations:
        idx = c.citation_id - 1
        if not (0 <= idx < len(retrieved)):
            return CheckResult(
                "citations_consistent", False, f"citation [{c.citation_id}] out of range"
            )
        src = retrieved[idx]
        if (c.doc_id, c.page_num, c.chunk_idx) != (src.doc_id, src.page_num, src.chunk_idx):
            return CheckResult(
                "citations_consistent",
                False,
                f"citation [{c.citation_id}] does not match its retrieved chunk",
            )
    return CheckResult("citations_consistent", True, "all citations consistent with retrieved_chunks")


def _check_fake_doc_in_top_k(ctx: CaseContext, params: dict) -> CheckResult:
    ok = bool(ctx.extra.get("fake_doc_in_top_k"))
    return CheckResult("fake_doc_in_top_k", ok, f"fake_doc_in_top_k={ok}")


def _check_payload_absent(ctx: CaseContext, params: dict) -> CheckResult:
    found = bool(ctx.extra.get("payload_found"))
    return CheckResult("payload_absent", not found, f"payload_found={found}")


def _check_payload_present(ctx: CaseContext, params: dict) -> CheckResult:
    found = bool(ctx.extra.get("payload_found"))
    return CheckResult("payload_present", found, f"payload_found={found}")


def _check_fake_doc_not_cited(ctx: CaseContext, params: dict) -> CheckResult:
    cited = bool(ctx.extra.get("fake_doc_cited_as_source"))
    return CheckResult("fake_doc_not_cited", not cited, f"fake_doc_cited_as_source={cited}")


def _check_leak_absent(ctx: CaseContext, params: dict) -> CheckResult:
    found = bool(ctx.extra.get("leak_found"))
    canaries = ctx.extra.get("leaked_canaries", ())
    detail = f"leak_found={found}" + (f", canaries={list(canaries)}" if found else "")
    return CheckResult("leak_absent", not found, detail)


def _check_faithful_to_context(ctx: CaseContext, params: dict) -> CheckResult:
    """LLM-as-judge groundedness check (REQ-FAITH-01, OWASP LLM09).

    Restricted to target ``ask`` by ``_CHECK_COMPATIBLE_TARGETS`` —
    needs ``retrieved_chunks`` on ``ctx.raw``, same convention already
    relied on by ``_check_citations_consistent``. Unlike every other
    check in this module, this one makes a second, independent LLM
    call — costly and non-deterministic, same posture as the targets
    themselves. A judge parse failure (``judge.JudgeParseError``) or
    network error is not caught here; ``run_case`` wraps check
    execution precisely so this surfaces as an ``ERROR`` result rather
    than crashing the whole run — that same wrap would also catch a
    genuine logic bug in a *different*, normally-pure check, which is
    an accepted, documented imprecision (see ``run_case``'s docstring):
    the exception class name is preserved in ``CaseResult.error`` so a
    reader can still tell a judge/network failure from a check bug.
    """
    retrieved = getattr(ctx.raw, "retrieved_chunks", ())
    model = params.get("judge_model", judge.JUDGE_MODEL)
    temperature = params.get("judge_temperature", judge.JUDGE_TEMPERATURE)
    verdict = judge.judge_faithfulness(
        ctx.question, list(retrieved), ctx.text, model=model, temperature=temperature
    )
    detail = (
        "faithful"
        if verdict.faithful
        else f"unsupported claim(s): {list(verdict.unsupported_claims)} — {verdict.reasoning}"
    )
    return CheckResult("faithful_to_context", verdict.faithful, detail)


CHECKS: dict[str, Callable[[CaseContext, dict], CheckResult]] = {
    "refusal_signal": _check_refusal_signal,
    "no_forbidden_terms": _check_no_forbidden_terms,
    "has_citation": _check_has_citation,
    "citations_consistent": _check_citations_consistent,
    "fake_doc_in_top_k": _check_fake_doc_in_top_k,
    "payload_absent": _check_payload_absent,
    "payload_present": _check_payload_present,
    "fake_doc_not_cited": _check_fake_doc_not_cited,
    "leak_absent": _check_leak_absent,
    "faithful_to_context": _check_faithful_to_context,
}


# --- Runner ---------------------------------------------------------------


@dataclass(frozen=True)
class CaseResult:
    """Outcome of running one :class:`Case` — evidence, not just a verdict.

    ``error`` is set when the target itself raised (network failure,
    missing API key, model load failure, ...) — distinct from a check
    failing, which means the target ran fine but the observed behaviour
    diverged from what the case declares. The remaining fields
    (``model`` through ``timestamp``) mirror ``ask.Answer``'s
    instrumentation so a case result is reproducible/auditable evidence
    for the Brique 7 VCD, not a bare bool.
    """

    case: Case
    check_results: tuple[CheckResult, ...] = ()
    error: str | None = None
    text: str = ""
    model: str | None = None
    temperature: float | None = None
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    timestamp: str = ""

    @property
    def observed(self) -> str:
        """``"PASS"`` iff every check passed, else ``"FAIL"``. Undefined
        (never read) when ``error`` is set — see :attr:`status`."""
        return "PASS" if all(cr.passed for cr in self.check_results) else "FAIL"

    @property
    def status(self) -> str:
        """One of ``ERROR``, ``PASS``, ``TRACKED-FAIL``, ``REGRESSION``,
        ``UNEXPECTED-PASS`` — observed outcome reconciled against
        :attr:`Case.expected`. A case declared ``expected: FAIL`` (a
        known, tracked vulnerability) failing its checks is
        ``TRACKED-FAIL``, not a bench failure; the same case newly
        *passing* its checks is ``UNEXPECTED-PASS`` — the tracked
        vulnerability apparently disappeared without the case being
        updated, which is exactly the silent-drift class this project
        refuses to let slide.
        """
        if self.error is not None:
            return "ERROR"
        observed = self.observed
        if observed == self.case.expected:
            return "PASS" if self.case.expected == "PASS" else "TRACKED-FAIL"
        return "REGRESSION" if self.case.expected == "PASS" else "UNEXPECTED-PASS"

    @property
    def passed(self) -> bool:
        """True iff the observed outcome matches the declared ``expected``
        one (``PASS`` or ``TRACKED-FAIL``) — the single boolean a CI-style
        gate would check."""
        return self.status in ("PASS", "TRACKED-FAIL")


def run_case(case: Case) -> CaseResult:
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        ctx = TARGETS[case.target](case.input)
    except Exception as exc:  # boundary: LLM call, model load, network
        return CaseResult(case=case, error=f"{exc.__class__.__name__}: {exc}", timestamp=timestamp)
    try:
        # Boundary again: since Brique 6, a check can itself call an LLM
        # (``faithful_to_context`` → the judge) — no longer guaranteed
        # pure/local like the original substring/citation checks. Wrapped
        # here so a judge network/parse failure surfaces as an ``ERROR``
        # CaseResult (with the already-succeeded ctx's provenance
        # preserved) instead of crashing the whole bench run. Accepted
        # imprecision: this also buckets a genuine bug in an otherwise-pure
        # check (e.g. a ``KeyError`` in a check's own logic) into the same
        # ``ERROR`` status as a judge/network failure — ``exc.__class__.__name__``
        # is kept in ``CaseResult.error`` precisely so that distinction is
        # still readable from the evidence, without a separate error-taxonomy
        # field that this baseline doesn't yet need.
        results = tuple(CHECKS[spec.type](ctx, spec.params) for spec in case.checks)
    except Exception as exc:
        return CaseResult(
            case=case,
            error=f"{exc.__class__.__name__}: {exc}",
            text=ctx.text,
            model=ctx.model,
            temperature=ctx.temperature,
            latency_ms=ctx.latency_ms,
            tokens_in=ctx.tokens_in,
            tokens_out=ctx.tokens_out,
            timestamp=timestamp,
        )
    return CaseResult(
        case=case,
        check_results=results,
        text=ctx.text,
        model=ctx.model,
        temperature=ctx.temperature,
        latency_ms=ctx.latency_ms,
        tokens_in=ctx.tokens_in,
        tokens_out=ctx.tokens_out,
        timestamp=timestamp,
    )


def run_cases(cases: Iterable[Case]) -> list[CaseResult]:
    return [run_case(c) for c in cases]


def _print_report(results: list[CaseResult]) -> None:
    for r in results:
        print(
            f"[{r.status}] {r.case.id} ({r.case.requirement}, "
            f"expected={r.case.expected}) — {r.case.title}"
        )
        if r.error:
            print(f"    ERROR: {r.error}")
            continue
        for cr in r.check_results:
            mark = "ok " if cr.passed else "!! "
            print(f"    {mark}{cr.type}: {cr.detail}")

    n_pass = sum(1 for r in results if r.passed)
    print(f"\n{n_pass}/{len(results)} case(s) at expected outcome.")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the Brique 5 IVVQ bench against bench/cases/*.yaml."
    )
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=DEFAULT_CASES_DIR,
        help="Directory of YAML test cases (default: bench/cases/).",
    )
    args = parser.parse_args()

    cases = load_cases(args.cases_dir)
    results = run_cases(cases)
    _print_report(results)
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
