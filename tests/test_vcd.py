"""Tests for the Brique 7 VCD generator — pure Markdown rendering, zero network.

``vcd.render_vcd`` only formats already-produced ``CaseResult`` evidence,
so every test fabricates ``Case``/``CaseResult`` objects directly — no
YAML files, no LLM calls, no ``bench_runner.run_case``. Same convention
``tests/test_bench_runner.py`` uses for check logic. ``vcd.build_vcd`` /
``vcd.main`` (the real-run path that hits ``bench_runner.load_cases`` +
``run_cases`` against a live model) are not covered here — same
"not covered by CI" posture as ``bench_runner.py`` itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from bench_runner import Case, CaseResult, CheckResult, CheckSpec
from vcd import build_vcd, render_vcd

FIXED_TIME = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)


def _case(**overrides) -> Case:
    base = dict(
        id="TEST-01",
        requirement="REQ-TEST-01",
        title="A test case",
        description="",
        target="ask",
        input={"question": "Une question ?"},
        checks=(CheckSpec(type="has_citation"),),
        expected="PASS",
        source_path=Path("bench/cases/test-01.yaml"),
    )
    base.update(overrides)
    return Case(**base)


def _result(case: Case, *, check_results: tuple = (), error: str | None = None, **overrides) -> CaseResult:
    base = dict(
        case=case,
        check_results=check_results,
        error=error,
        text="answer text",
        model="anthropic/claude-haiku-4-5",
        temperature=0.0,
        latency_ms=1234,
        tokens_in=100,
        tokens_out=50,
        timestamp="2026-07-14T12:00:00+00:00",
    )
    base.update(overrides)
    return CaseResult(**base)


def test_render_vcd_includes_title_and_generated_timestamp():
    md = render_vcd([], generated_at=FIXED_TIME)
    assert "# Verification Control Document" in md
    assert "2026-07-14T12:00:00+00:00" in md
    assert "**Cases run** : 0" in md


def test_render_vcd_summary_counts_each_status():
    pass_case = _case(id="A", expected="PASS")
    tracked_fail_case = _case(id="B", expected="FAIL")
    regression_case = _case(id="C", expected="PASS")
    unexpected_pass_case = _case(id="D", expected="FAIL")

    results = [
        _result(pass_case, check_results=(CheckResult("x", True, "ok"),)),
        _result(tracked_fail_case, check_results=(CheckResult("x", False, "still failing"),)),
        _result(regression_case, check_results=(CheckResult("x", False, "broke"),)),
        _result(unexpected_pass_case, check_results=(CheckResult("x", True, "vulnerability gone"),)),
    ]
    md = render_vcd(results, generated_at=FIXED_TIME)

    assert "| PASS | 1 |" in md
    assert "| TRACKED-FAIL | 1 |" in md
    assert "| REGRESSION | 1 |" in md
    assert "| UNEXPECTED-PASS | 1 |" in md
    assert "| ERROR | 0 |" in md


def test_render_vcd_overall_verdict_compliant_when_all_pass_or_tracked_fail():
    ok_case = _case(id="A", expected="PASS")
    tracked_case = _case(id="B", expected="FAIL")
    results = [
        _result(ok_case, check_results=(CheckResult("x", True, "ok"),)),
        _result(tracked_case, check_results=(CheckResult("x", False, "still failing"),)),
    ]
    md = render_vcd(results, generated_at=FIXED_TIME)
    assert "COMPLIANT — 2/2 case(s)" in md
    assert "NON-COMPLIANT" not in md


def test_render_vcd_overall_verdict_non_compliant_on_regression():
    c = _case(id="A", expected="PASS")
    results = [_result(c, check_results=(CheckResult("x", False, "broke"),))]
    md = render_vcd(results, generated_at=FIXED_TIME)
    assert "NON-COMPLIANT" in md


def test_render_vcd_overall_verdict_non_compliant_on_error():
    c = _case(id="A")
    results = [_result(c, error="RuntimeError: network down")]
    md = render_vcd(results, generated_at=FIXED_TIME)
    assert "NON-COMPLIANT" in md


def test_render_vcd_traceability_matrix_lists_requirement_and_case_id():
    c = _case(id="DEMO-CASE", requirement="REQ-DEMO-01")
    results = [_result(c, check_results=(CheckResult("x", True, "ok"),))]
    md = render_vcd(results, generated_at=FIXED_TIME)
    assert "REQ-DEMO-01" in md
    assert "`DEMO-CASE`" in md


def test_render_vcd_case_detail_lists_each_check_with_pass_fail_marker():
    c = _case(id="A")
    results = [
        _result(
            c,
            check_results=(
                CheckResult("first_check", True, "detail one"),
                CheckResult("second_check", False, "detail two"),
            ),
        )
    ]
    md = render_vcd(results, generated_at=FIXED_TIME)
    assert "- [x] `first_check` — detail one" in md
    assert "- [ ] `second_check` — detail two" in md


def test_render_vcd_error_case_shows_error_and_omits_checks_section():
    c = _case(id="A")
    results = [_result(c, error="RuntimeError: network down")]
    md = render_vcd(results, generated_at=FIXED_TIME)
    assert "- **Error**: RuntimeError: network down" in md
    assert "observed: N/A" in md
    assert "**Checks**:" not in md


def test_render_vcd_case_detail_includes_run_provenance():
    c = _case(id="A")
    results = [_result(c, check_results=(CheckResult("x", True, "ok"),))]
    md = render_vcd(results, generated_at=FIXED_TIME)
    assert "`anthropic/claude-haiku-4-5`" in md
    assert "T=0.0" in md
    assert "1234 ms" in md
    assert "100 in / 50 out" in md


def test_render_vcd_escapes_pipe_characters_in_table_cells():
    c = _case(id="A", title="Weird | title")
    results = [_result(c, check_results=(CheckResult("x", True, "ok"),))]
    md = render_vcd(results, generated_at=FIXED_TIME)
    assert "Weird \\| title" in md


def test_render_vcd_empty_results_is_vacuously_compliant():
    md = render_vcd([], generated_at=FIXED_TIME)
    assert "COMPLIANT — 0/0 case(s)" in md


def test_render_vcd_summary_counts_error_case():
    c = _case(id="A")
    results = [_result(c, error="RuntimeError: network down")]
    md = render_vcd(results, generated_at=FIXED_TIME)
    assert "| ERROR | 1 |" in md


def test_render_vcd_case_detail_includes_description_when_present():
    c = _case(id="A", description="Checks that the fake document is not cited.")
    results = [_result(c, check_results=(CheckResult("x", True, "ok"),))]
    md = render_vcd(results, generated_at=FIXED_TIME)
    assert "- **Description**: Checks that the fake document is not cited." in md


def test_render_vcd_escapes_newlines_in_table_cells():
    c = _case(id="A", title="Multi\nline title")
    results = [_result(c, check_results=(CheckResult("x", True, "ok"),))]
    md = render_vcd(results, generated_at=FIXED_TIME)
    assert "Multi line title" in md
    assert "Multi\nline title" not in md


def test_render_vcd_case_detail_order_matches_traceability_matrix_order():
    # Fed in reverse-requirement order; both sections must render sorted
    # by (requirement, case id) so a reader can cross-reference them.
    c_b = _case(id="B", requirement="REQ-B-01")
    c_a = _case(id="A", requirement="REQ-A-01")
    results = [
        _result(c_b, check_results=(CheckResult("x", True, "ok"),)),
        _result(c_a, check_results=(CheckResult("x", True, "ok"),)),
    ]
    md = render_vcd(results, generated_at=FIXED_TIME)
    matrix_pos_a = md.index("REQ-A-01")
    matrix_pos_b = md.index("REQ-B-01")
    detail_pos_a = md.index("### `A`")
    detail_pos_b = md.index("### `B`")
    assert matrix_pos_a < matrix_pos_b
    assert detail_pos_a < detail_pos_b


def test_render_vcd_includes_config_identification_when_provided():
    c = _case(id="A")
    results = [_result(c, check_results=(CheckResult("x", True, "ok"),))]
    md = render_vcd(
        results,
        generated_at=FIXED_TIME,
        git_sha="abc1234",
        corpus_sha256="deadbeef" * 8,
    )
    assert "**Harness commit** : `abc1234`" in md
    assert f"**Corpus baseline** : `{'deadbeef' * 8}`" in md


def test_render_vcd_config_identification_defaults_to_unknown():
    md = render_vcd([], generated_at=FIXED_TIME)
    assert "**Harness commit** : `unknown`" in md
    assert "**Corpus baseline** : `unknown`" in md


def test_render_vcd_overall_line_glosses_tracked_fail_when_present():
    tracked_case = _case(id="A", expected="FAIL")
    results = [_result(tracked_case, check_results=(CheckResult("x", False, "still failing"),))]
    md = render_vcd(results, generated_at=FIXED_TIME)
    assert "includes tracked, known vulnerabilities" in md


def test_render_vcd_overall_line_omits_gloss_when_no_tracked_fail():
    ok_case = _case(id="A", expected="PASS")
    results = [_result(ok_case, check_results=(CheckResult("x", True, "ok"),))]
    md = render_vcd(results, generated_at=FIXED_TIME)
    assert "includes tracked, known vulnerabilities" not in md


def test_build_vcd_raises_on_empty_cases_dir(tmp_path):
    with pytest.raises(ValueError, match="no test cases found"):
        build_vcd(tmp_path)
