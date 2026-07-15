# Changelog

Formatted as [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [SemVer](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-07-15

**Milestone**: Briques 0–6 shipped end-to-end. 17 `REQ-*` catalogued in
the frozen registry ([`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md)) —
15 pre-B6 (`REQ-CORPUS-*` × 4, `REQ-CHUNK-*` × 4, `REQ-EMBED-*` × 2,
`REQ-RETRIEVE-01`, `REQ-RAG-*` × 2, `REQ-INJECT-01`, `REQ-BENCH-01`)
plus the two added here (`REQ-LEAK-01`, `REQ-FAITH-01`). Coverage of
three OWASP LLM Top 10 families: LLM01 (indirect prompt injection),
LLM02 (sensitive information disclosure), LLM09 (overreliance /
hallucination). Briques 7–9 are documented in
[`BACKLOG_RAG.md`](BACKLOG_RAG.md) and deferred to v1.1+ per the
project's cross-repo sequencing decision.

### Added — Brique 6

- **`REQ-LEAK-01`** — OWASP LLM02 sensitive-information-disclosure case.
  New attack file `corpus_attack/fake-note-canary.md` (plausible ANSSI
  note *DIV-2026-B7* framed as internal / non-restituable) carrying the
  canary token `LEAK-CANARY-9X4B7`. YAML case
  `bench/cases/req-leak-01-canary-not-echoed.yaml` wires it through the
  parametrised `injection_demo` target with `expected: PASS` — a
  well-disciplined RAG must refuse to echo the token.
- **`REQ-FAITH-01`** — OWASP LLM09 faithfulness check. New module
  `faithfulness_judge.py` implementing an LLM-as-judge with a
  JSON-strict verdict shape `{grounded: bool, reason: str}`, an
  injectable `chat_completion` callable for deterministic tests, and a
  fallback path when no citation resolved. New bench check
  `faithful_to_cited_chunks` delegating to the judge. YAML case
  `bench/cases/req-faith-01-answer-grounded-in-cited-chunks.yaml`
  targeting `ask` with `expected: PASS`.
- Parametrisation of `demo_injection.run_demo` — new optional
  `attack_doc_path` / `attack_doc_id` / `payload_needle` params.
  Defaults preserve the Brique 4 constants; the two committed
  INJECT-01 YAML cases and every existing test are unchanged. This
  parametrisation is what lets the LEAK-01 case reuse the
  `benign ∪ attack` retrieval pipeline instead of forking a second
  demo module.
- `BACKLOG_RAG.md` at repo root — documents deferred Briques 7-9 and
  the natural B6 extensions (multi-turn / encoded injection variants,
  per-check `expected` granularity, judge hardening, drift automation)
  as a frozen contract rather than a live TODO.
- `CHANGELOG.md` (this file).
- New deterministic tests:
  `tests/test_faithfulness_judge.py` (12 tests covering
  `_cited_chunks`, `_parse_verdict_json`, and `judge()` with a stubbed
  chat completion), and extensions to
  `tests/test_bench_runner.py` and `tests/test_demo_injection.py`.

### Changed

- README roadmap ticks B6; `Status` section updated to *v1.0* with
  the six committed YAML cases and 13 enforced `REQ-*`. Briques 7-9
  are moved to a v1.1+ pointer to `BACKLOG_RAG.md`.
- `docs/DESIGN.md` gains a new section for Brique 6 documenting the
  parametrisation reuse, the check-reuse choice for LEAK
  (`no_forbidden_terms`, not a new dedicated check), and the LLM-judge
  design trade-offs for FAITH-01.
- `docs/REQUIREMENTS.md` extended with `REQ-LEAK-01` and
  `REQ-FAITH-01`, and its *Statut* section updated: still frozen (no
  renaming, no rewriting of prior IDs — additions only), scope now
  covers 17 `REQ-*`.

### Notes

- No behavior change on any pre-existing case, test, or artefact.
  Backwards compatibility is enforced by construction: `run_demo`'s
  new parameters default to the old constants, no schema field was
  renamed, no check type was removed.
- Two new LLM-costing paths (real FAITH-01 case, real LEAK-01 case)
  are excluded from CI same as the prior integration tests — CI runs
  the deterministic subset only. Manual bench execution via
  `python bench_runner.py` remains the auditable path.

---

*Prior briques (0–5) predate this changelog and are documented in
[`docs/DESIGN.md`](docs/DESIGN.md) and [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md).*
