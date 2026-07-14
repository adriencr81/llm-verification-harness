#!/usr/bin/env python3
"""Verification Control Document generator — Brique 7.

To be catalogued as ``REQ-VCD-01`` in ``docs/REQUIREMENTS.md``. Turns a
batch of :class:`bench_runner.CaseResult` (Brique 5's runner, extended
in Brique 6) into a Markdown dossier: a status summary, a
requirement-to-case traceability matrix, and a per-case detail section
with the exact evidence (checks, model, temperature, latency, tokens,
timestamp) each ``CaseResult`` already carries.

Two layers, deliberately split like ``bench_runner.run_case`` (logic)
vs. ``bench_runner.main`` (CLI that hits a real LLM):

- :func:`render_vcd` is a pure function — no I/O, no LLM call. It only
  formats ``CaseResult`` objects that were already produced elsewhere.
  This is what makes it testable in CI with zero network access:
  ``tests/test_vcd.py`` fabricates ``Case``/``CaseResult`` directly,
  the same convention ``tests/test_bench_runner.py`` uses for check
  logic.
- :func:`build_vcd` / :func:`main` load the real committed cases via
  ``bench_runner.load_cases``, run them via ``bench_runner.run_cases``
  (real LLM calls, BGE-M3 loads, a second LLM call for the
  ``faithful_to_context`` judge), and hand the result to
  :func:`render_vcd`. Same "not covered by CI" posture as
  ``bench_runner.py`` itself.

**No new status vocabulary.** The VCD reuses exactly the five
``CaseResult.status`` values ``REQ-BENCH-01`` already defines (``PASS``,
``TRACKED-FAIL``, ``REGRESSION``, ``UNEXPECTED-PASS``, ``ERROR``) and
derives one binary top-line verdict from ``CaseResult.passed``:
``COMPLIANT`` if every case is at its expected outcome, ``NON-COMPLIANT``
otherwise. A reviewer who already knows the bench has nothing new to
learn to read the dossier.

**Not committed as a frozen artifact.** Unlike ``pages.jsonl`` /
``chunks.jsonl`` (algorithmic, SHA256-locked), a generated VCD is
downstream of live LLM calls — not reproducible bit-for-bit across
runs, same "contract by properties" reasoning as the embeddings (B2)
and RAG (B3) baselines. Each run of ``python vcd.py`` overwrites
``docs/VCD.md``; ``git diff`` on that file is the human audit trail,
same discipline already used for the SHA256-locked corpus artifacts.

Usage::

    python vcd.py                    # run every case, write docs/VCD.md
    python vcd.py --cases-dir DIR --output PATH
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

import bench_runner

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "VCD.md"
DEFAULT_MANIFEST = REPO_ROOT / "corpus" / "manifest.yaml"

_STATUS_ORDER = ("PASS", "TRACKED-FAIL", "REGRESSION", "UNEXPECTED-PASS", "ERROR")
_UNKNOWN = "unknown"


def _escape_cell(text: str) -> str:
    """Escape a string for embedding in a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _git_sha(repo_root: Path = REPO_ROOT) -> str:
    """Best-effort git commit identifying the harness version under test.

    There is no separate semver scheme in this repo — the commit *is*
    the version identifier, same convention ``producer_env`` uses to
    pin library versions elsewhere in the manifest. Never raises:
    config identification degrades to ``"unknown"`` rather than
    blocking VCD generation over a missing ``git`` binary or a
    non-repo checkout.
    """
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        return f"{sha}-dirty" if dirty else sha
    except Exception:
        return _UNKNOWN


def _corpus_chunks_sha256(manifest_path: Path = DEFAULT_MANIFEST) -> str:
    """SHA256 of the committed corpus baseline (``REQ-CHUNK-03``) the run
    was verified against — the exact artifact retrieval/RAG/attack
    demos consume, not the whole manifest file. Never raises: falls
    back to ``"unknown"`` if the manifest is missing or malformed.
    """
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        return manifest["derived_artifacts"]["chunks_jsonl"]["sha256"]
    except Exception:
        return _UNKNOWN


def render_vcd(
    results: list[bench_runner.CaseResult],
    *,
    generated_at: datetime | None = None,
    git_sha: str = _UNKNOWN,
    corpus_sha256: str = _UNKNOWN,
) -> str:
    """Render a batch of :class:`bench_runner.CaseResult` as a Markdown VCD.

    Pure — no I/O, no LLM call, deterministic given fixed inputs
    (``generated_at`` defaults to ``datetime.now(timezone.utc)`` for
    the CLI path; every test in ``tests/test_vcd.py`` passes an
    explicit value). ``git_sha``/``corpus_sha256`` are the
    configuration-identification fields a VCD needs to answer "which
    baseline was this run verified against?" — plain strings here so
    this function stays I/O-free; :func:`build_vcd` computes them via
    :func:`_git_sha`/:func:`_corpus_chunks_sha256` and passes them in.
    """
    generated_at = generated_at or datetime.now(timezone.utc)
    counts = {status: 0 for status in _STATUS_ORDER}
    for r in results:
        counts[r.status] += 1
    n_pass = sum(1 for r in results if r.passed)
    overall = "COMPLIANT" if n_pass == len(results) else "NON-COMPLIANT"
    overall_line = f"**Overall** : {overall} — {n_pass}/{len(results)} case(s) at expected outcome"
    if counts["TRACKED-FAIL"] > 0:
        overall_line += " (includes tracked, known vulnerabilities — see Summary)"

    ordered = sorted(results, key=lambda r: (r.case.requirement, r.case.id))

    lines: list[str] = []
    lines.append("# Verification Control Document — llm-verification-harness")
    lines.append("")
    lines.append(f"**Generated** : {generated_at.isoformat()}")
    lines.append(f"**Harness commit** : `{git_sha}`")
    lines.append(f"**Corpus baseline** : `{corpus_sha256}` (`corpus/chunks.jsonl`, REQ-CHUNK-03)")
    lines.append(f"**Cases run** : {len(results)}")
    lines.append(overall_line)
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|---|---|")
    for status in _STATUS_ORDER:
        lines.append(f"| {status} | {counts[status]} |")
    lines.append("")

    lines.append("## Traceability matrix")
    lines.append("")
    lines.append("| Requirement | Case | Status | Expected | Title |")
    lines.append("|---|---|---|---|---|")
    for r in ordered:
        lines.append(
            f"| {_escape_cell(r.case.requirement)} | `{_escape_cell(r.case.id)}` | "
            f"{r.status} | {r.case.expected} | {_escape_cell(r.case.title)} |"
        )
    lines.append("")

    lines.append("## Case detail")
    lines.append("")
    for r in ordered:
        lines.append(f"### `{r.case.id}` — {r.case.requirement}")
        lines.append("")
        lines.append(f"- **Title**: {_escape_cell(r.case.title)}")
        if r.case.description:
            lines.append(f"- **Description**: {_escape_cell(r.case.description)}")
        lines.append(f"- **Target**: `{r.case.target}`")
        observed = r.observed if r.error is None else "N/A"
        lines.append(f"- **Status**: {r.status} (expected: {r.case.expected}, observed: {observed})")
        lines.append(f"- **Source**: `{r.case.source_path}`")
        if r.error is not None:
            lines.append(f"- **Error**: {_escape_cell(r.error)}")
        else:
            lines.append(
                f"- **Model**: `{r.model}` (T={r.temperature}) — "
                f"**Latency**: {r.latency_ms} ms — **Tokens**: {r.tokens_in} in / {r.tokens_out} out"
            )
            lines.append(f"- **Timestamp**: {r.timestamp}")
            lines.append("")
            lines.append("**Checks**:")
            lines.append("")
            for cr in r.check_results:
                mark = "x" if cr.passed else " "
                lines.append(f"- [{mark}] `{cr.type}` — {_escape_cell(cr.detail)}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "LLM output is not bit-for-bit reproducible (provider routing, "
        "floating-point non-associativity) even at temperature 0 — same "
        '"contract by properties" posture as the embeddings and RAG '
        "baselines (see `docs/REQUIREMENTS.md`). Re-running this "
        "generator against the same cases can surface a different "
        "`status` on any check backed by a live LLM call, with no code "
        "change."
    )
    lines.append("")

    return "\n".join(lines)


def build_vcd(
    cases_dir: Path = bench_runner.DEFAULT_CASES_DIR,
) -> tuple[str, list[bench_runner.CaseResult]]:
    """Load, run, and render every case in ``cases_dir``.

    Hits a real LLM (and, for ``injection_demo``/``leak_demo``, loads
    BGE-M3) via ``bench_runner.run_cases`` — same network/model-load
    cost as ``bench_runner.main``, not covered by CI.

    Refuses an empty ``cases_dir`` rather than rendering a vacuously
    ``COMPLIANT`` dossier over zero cases — for the flagship
    deliverable, a false-green from a mistyped path is worse than a
    loud failure.
    """
    cases = bench_runner.load_cases(cases_dir)
    if not cases:
        raise ValueError(
            f"no test cases found under {cases_dir} — refusing to generate "
            "a vacuously-compliant VCD"
        )
    results = bench_runner.run_cases(cases)
    markdown = render_vcd(
        results, git_sha=_git_sha(), corpus_sha256=_corpus_chunks_sha256()
    )
    return markdown, results


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate the Brique 7 Verification Control Document from bench/cases/*.yaml."
    )
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=bench_runner.DEFAULT_CASES_DIR,
        help="Directory of YAML test cases (default: bench/cases/).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to write the generated VCD (default: docs/VCD.md).",
    )
    args = parser.parse_args()

    markdown, results = build_vcd(args.cases_dir)
    args.output.write_text(markdown, encoding="utf-8")
    n_pass = sum(1 for r in results if r.passed)
    print(f"VCD written to {args.output} — {n_pass}/{len(results)} case(s) at expected outcome.")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
