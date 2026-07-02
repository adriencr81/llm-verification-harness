# llm-verification-harness

Aerospace-grade verification methodology applied to RAG/LLM evaluation.

A formal verification harness for retrieval-augmented LLM systems, built brique
by brique to demonstrate how safety-critical IVVQ practices transpose to
non-deterministic AI substrates. Coverage targets OWASP LLM Top 10 and MITRE
ATLAS. The signature deliverable is a Verification Control Document (VCD)
generated automatically from each test run.

## Why this project

Most existing LLM evaluation frameworks (Giskard, LM Eval Harness, OpenAI
Evals) provide test runners. None of them produce a verification dossier in
the form required by aerospace and defense industries — traceability matrix,
acceptance criteria per requirement, evidence per test, formal verdict.

This project transposes that formalism to RAG/LLM systems.

## Roadmap

Built incrementally, one brique per week:

- [x] Brique 0 — Project skeleton + first LLM call (observe non-determinism)
- [ ] Brique 1 — Document ingestion, chunking, provenance tracking *(in progress
      — corpus contract shipped; PDF extraction + chunking next)*
- [ ] Brique 2 — Embeddings and semantic retrieval (cosine similarity)
- [ ] Brique 3 — Full RAG pipeline (question → retrieve → generate)
- [ ] Brique 4 — OWASP LLM01 (indirect prompt injection) test
- [ ] Brique 5 — IVVQ-style test case formalization (YAML runner)
- [ ] Brique 6 — Leak (LLM02), faithfulness (LLM09, LLM-as-judge), drift
- [ ] Brique 7 — Auto-generated Verification Control Document
- [ ] Brique 8 — Catalog (OWASP/ATLAS), unit tests, CI, polished README
- [ ] Brique 9 — Hardening loop (detect → fix → re-verify on injection)

## Corpus contract (Brique 1 — in progress)

The corpus is 11 ANSSI cybersecurity guides, versioned in
[`corpus/manifest.yaml`](corpus/manifest.yaml) as a **hash-locked contract**
rather than as opaque binary blobs. Any silent modification of a PDF is
refused by construction. See [`corpus/README.md`](corpus/README.md) for the
IVVQ rationale, the deliberate "bump conscient" procedure, and licensing.

Three requirements are catalogued in
[`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md), each with an explicit
enforcement status — the README does not conflate "declared" with
"enforced":

- **`REQ-CORPUS-01` — SHA256 non-alteration** *(enforced)*. Consumer:
  `download_corpus.verify_document`. Violations surfaced as
  `CorpusIntegrityError`. Falsifiability test that will be cited by the
  VCD (Brique 7):
  [`test_single_byte_alteration_raises_integrity_error`](tests/test_download_corpus.py).
- **`REQ-CORPUS-03` — File-size sanity check** *(opt-in, enforced when
  `bytes` is declared)*. Fail-fast filter against wrong-file swap or
  truncation — never a substitute for SHA256. Consumer:
  `download_corpus.verify_document`. Violations surfaced as
  `CorpusSizeError`.
- **`REQ-CORPUS-02` — Page-count invariant** *(declared, consumer pending
  Brique 2/3)*. Chunk provenance `(doc_id, page=N)` must satisfy
  `N ≤ pages(doc)`. The `pages` field is frozen in the manifest by
  `enrich_manifest.py` today; enforcement moves to chunk consumers when
  they land.

The manifest-enrichment tool `enrich_manifest.py` is itself covered by
IVVQ-style tests:
**bit-for-bit idempotence** (a 2nd run must not touch the file), **atomic
write** (a missing PDF must leave the manifest untouched), unique-SHA256
precondition, header-comment preservation, and a matcher regex
non-regression sentinel.

> The corpus rationale documents (`corpus/README.md`, `docs/REQUIREMENTS.md`)
> and the code docstrings are in French: target market is French
> defense/aerospace. The root README stays in English as an international
> entry point.

## Status

Brique 0 complete. Brique 1 in progress — corpus contract foundation
delivered (REQ-CORPUS-01 enforced, REQ-CORPUS-03 enforced opt-in,
REQ-CORPUS-02 declared). Remaining before Brique 1 closes: `doc_id`
baseline test, PDF extraction via `pdfplumber`, chunking with attached
provenance, `chunks.json` deliverable.

## Stack

Python 3.13, OpenAI SDK via OpenRouter (Claude Sonnet 4.6 as primary LLM,
provider-agnostic by design).

## Getting started

Install dependencies (runtime + test):

```
pip install -r requirements.txt
```

Verify the corpus against its declared SHA256 and (opt-in) size:

```
python download_corpus.py
```

Enrich the manifest with `bytes` and `pages` after adding or bumping a
document (idempotent — no-op if all entries already carry both fields):

```
python enrich_manifest.py
```

Run the test suite:

```
python -m pytest -q
```
