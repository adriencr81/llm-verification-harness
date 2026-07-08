# llm-verification-harness

Aerospace-grade verification methodology applied to RAG/LLM evaluation.

A formal verification harness for retrieval-augmented LLM systems, built brique
by brique to demonstrate how safety-critical IVVQ practices transpose to
non-deterministic AI substrates. Coverage targets specific OWASP LLM Top 10
(2023) items — LLM01 (prompt injection), LLM02 (insecure output handling),
LLM09 (overreliance/faithfulness) — planned for Briques 4 and 6, not the full
Top 10. The signature deliverable is a Verification Control Document (VCD)
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
- [x] Brique 1 — Document ingestion, chunking, provenance tracking *(corpus
      contract, PDF extraction, persisted extraction & chunking baselines
      all shipped and SHA256-locked)*
- [ ] Brique 2 — Embeddings and semantic retrieval (cosine similarity)
- [ ] Brique 3 — Full RAG pipeline (question → retrieve → generate)
- [ ] Brique 4 — OWASP LLM01 (indirect prompt injection) test
- [ ] Brique 5 — IVVQ-style test case formalization (YAML runner)
- [ ] Brique 6 — Leak (LLM02), faithfulness (LLM09, LLM-as-judge), drift
- [ ] Brique 7 — Auto-generated Verification Control Document
- [ ] Brique 8 — Catalog (OWASP/ATLAS), unit tests, CI, polished README
- [ ] Brique 9 — Hardening loop (detect → fix → re-verify on injection)

## Corpus contract (Brique 1)

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
- **`REQ-CORPUS-02` — Page-count invariant** *(fully enforced)*. Chunk
  provenance `(doc_id, page=N)` must satisfy `N ≤ pages(doc)`. Three
  composed enforcements: `pages` frozen in the manifest by
  `enrich_manifest.py`; `extract_pdf.extract_doc` refuses to emit
  Pages whose count diverges from the manifest
  (`PageCountMismatchError`); per-doc page count in
  `corpus/pages.jsonl` asserted against the manifest at CI level;
  `chunk_pages.chunk_page` copies `page_num` verbatim from a
  contract-verified Page — never re-opens a PDF — so
  `chunk.page_num ∈ [1, manifest.pages]` holds by construction.
- **`REQ-CORPUS-04` — Persisted extraction baseline, SHA256-locked**
  *(enforced)*. PDF→text extraction is committed to
  [`corpus/pages.jsonl`](corpus/pages.jsonl) — one JSON record per
  page, in manifest order, 1-indexed — and its SHA256 is frozen in
  [`corpus/manifest.yaml`](corpus/manifest.yaml) under
  `derived_artifacts.pages_jsonl.sha256`. Symmetric to
  `REQ-CORPUS-01` on the source PDFs: silent extractor drift
  (including *text-only* drift that leaves counts and ordering
  intact) fails `test_baseline_hash_matches_manifest` at CI level,
  without depending on a human running `git diff`. LF line endings
  pinned across platforms by `.gitattributes`. Producer:
  `extract_all.extract_all`, under the `pdfplumber` version declared
  in `derived_artifacts.pages_jsonl.producer_env`.
- **`REQ-CHUNK-01` — Chunk size bounded** *(fully enforced)*. Every
  emitted chunk satisfies `token_count(chunk.text, cl100k_base) ≤ 800`.
  Enforced by the recursive character splitter (cascade
  `["\n\n","\n",". "," ",""]`) with a binary-search token-level
  fallback. Consumer: `chunk_pages.chunk_page`. Verified end-to-end
  on the versioned baseline via
  `test_baseline_every_chunk_under_max_tokens_on_real_corpus`.
- **`REQ-CHUNK-02` — Provenance strict-substring** *(fully enforced)*.
  Every chunk carries `(doc_id, page_num, chunk_idx, char_start,
  char_end)` such that `page.text[char_start:char_end] == chunk.text`
  exactly — no whitespace normalization, no rewriting. This is the
  falsifiability anchor that lets the VCD (Brique 7) verify a
  citation without touching a PDF. Verified on the versioned baseline
  via `test_baseline_strict_substring_invariant_on_real_corpus`.
- **`REQ-CHUNK-03` — Persisted chunking baseline, SHA256-locked**
  *(enforced)*. Chunker output committed to
  [`corpus/chunks.jsonl`](corpus/chunks.jsonl) (1239 chunks across
  833 pages / 11 docs). SHA256 frozen at
  `derived_artifacts.chunks_jsonl.sha256`, tokenizer and constants
  (`target_tokens=500`, `max_tokens=800`, `overlap_tokens=75`) pinned
  under `producer_env`. Any swap of tokenizer or constants moves the
  SHA256 deliberately. Producer: `chunk_pages.chunk_all`. Detection
  primaire via `test_chunks_baseline_hash_matches_manifest`.

### Known extraction quality (Brique 1)

The persisted baseline is a raw pdfplumber output — no post-processing
beyond the repetition-based header/footer strip described in
[`extract_pdf.py`](extract_pdf.py). Two known-noise patterns survive
in `corpus/pages.jsonl` today:

- **Cover pages** — several ANSSI guides render their title with a
  custom vertical layout that pdfplumber extracts one glyph per line
  (`"A\nN\nS\nS\nI\n..."`). Affects the first 1–2 pages of ~half the
  corpus. Fragmented but not lost.
- **`guide-hygiene.pdf`** — geometric layout with a single Y-bucket
  per page defeats the repetition-based header/footer stripper on
  its first pages. Documented as a `_strip_noise` limit in
  [`extract_pdf.py`](extract_pdf.py); frozen as a sentinel test
  (`test_extract_pages_hygiene_documented_limit_current_behavior`).

These are consequences of the current extractor's design trade-offs,
not silent failures — they are characterized here so the retrieval
evaluation in Brique 2 can measure their impact on chunk quality
before deciding whether a targeted regex pass or an OCR fallback is
worth adding.

### Chunking contract (Brique 1)

The chunker in [`chunk_pages.py`](chunk_pages.py) reads
[`corpus/pages.jsonl`](corpus/pages.jsonl) — never re-opens a PDF —
and emits `corpus/chunks.jsonl` under a stable, documented contract:

- **Recursive character splitter** with cascade
  `["\n\n","\n",". "," ",""]`, ~120 lines of pure Python, zero
  external dependency beyond `tiktoken`. LangChain-equivalent
  semantics, deliberately not LangChain: chosen for VCD auditability
  — a defense reviewer reads the whole splitter end-to-end in
  minutes, versus multiple layers of a third-party package that
  evolves independently on its own release cycle.
- **Tokenizer = `cl100k_base` (tiktoken)** — deliberate choice
  documented in the manifest's `producer_env`. Independent of the
  embedding model chosen for Brique 2 (mistral-embed, BGE-M3, …) so
  the chunk boundary does not have to move every time B2 iterates.
  Swappable via a future `--tokenizer` flag if B4 evaluation reveals
  a material retrieval bias.
- **Bounded chunks** — target 500 tokens, hard ceiling 800.
- **Overlap ~15% (75 tokens)** — insurance against the classic RAG
  bug where a semantic unit (an ANSSI recommendation) is bisected
  between two chunks and appears complete in neither. Intra-page
  only.
- **No cross-page chunks** — `chunk_page` is called per-page;
  overlap and split operate exclusively on a single page's text.
- **Provenance minimale, falsifiable** — `(doc_id, page_num,
  chunk_idx, char_start, char_end)` on every chunk. No structured
  section detection (no `R7`-style hierarchical anchor): the section
  is often present in the chunk text itself, and a best-effort regex
  would be untested clutter. Deliberate scoping decision — added
  later if retrieval evaluation motivates it.

### Known chunking limits (Brique 1)

Two behaviors of the chunker are characterized here as documented
limits — surfaced explicitly so Brique 2 (embedding + retrieval)
can measure their impact and decide on a resolution:

- **Micro-chunks on cover / section-header pages** — a handful of
  chunks fall below 10 tokens (`"ANNEXES"`, `"BIBLIOGRAPHIE"`, and
  similar mono-title pages that are extracted faithfully but are
  semantically thin). Not a chunker bug — the source pages
  genuinely contain nothing else. These are expected to score
  artificially high on retrieval queries containing the exact
  keyword and pollute top-K. Decision deferred to Brique 2: either a
  `MIN_TOKENS_PER_CHUNK` fusion into the previous/next chunk, or an
  indexing-time filter, depending on retrieval evaluation output.
- **Overlap degrades to zero on adjacent oversized atoms** — when
  two consecutive atoms are each above `TARGET_TOKENS`, the merger
  emits them as separate chunks with no shared text between them.
  The overlap policy targets the "one semantic unit bisected across
  two chunks, complete in neither" failure mode, which cannot
  happen when the unit itself is a full atom emitted alone (it is
  complete in that chunk). Fixing this would require intra-atom
  hard-split for the overlap tail — declined as disproportionate.
  Frozen by
  `test_merge_overlap_degrades_to_zero_on_adjacent_oversized_atoms`
  so any future change is caught explicitly.

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

Brique 0 complete. **Brique 1 complete** — corpus contract, PDF
extraction, and chunking all shipped under IVVQ discipline:
REQ-CORPUS-01/03 enforced, REQ-CORPUS-02 fully enforced (extraction
+ persistence + chunk boundary), REQ-CORPUS-04 enforced,
REQ-CHUNK-01/02/03 enforced. Two persisted baselines committed and
SHA256-locked: [`corpus/pages.jsonl`](corpus/pages.jsonl) (833 pages
across 11 ANSSI guides) and [`corpus/chunks.jsonl`](corpus/chunks.jsonl)
(1239 chunks with strict-substring provenance into pages.jsonl).
Brique 2 (embeddings) consumes `chunks.jsonl` next.

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

Extract the full corpus to `corpus/pages.jsonl` (deterministic,
manifest-ordered, 1-indexed pages, header/footer stripped by
**repetition** rather than geometry — see design rationale in
`extract_pdf.py` module docstring — page count checked against
manifest per REQ-CORPUS-02, persisted at rest per REQ-CORPUS-04):

```
python extract_all.py
```

Chunk the extracted pages to `corpus/chunks.jsonl` (recursive
character splitter, `cl100k_base` tokenizer, 500-token target with
75-token overlap, strict-substring provenance per REQ-CHUNK-02,
SHA256-locked per REQ-CHUNK-03):

```
python chunk_pages.py
```

Both output files are versioned; a fresh clone inherits the
committed baselines without re-running pdfplumber or the chunker.
Regenerate only when [`corpus/manifest.yaml`](corpus/manifest.yaml),
the extractor, or the chunker changes — the `git diff` on
`pages.jsonl` / `chunks.jsonl` is then the audit artefact, and the
SHA256 in the manifest must be bumped in the same PR (deliberate
gesture, not an accident).

To extract or chunk programmatically:

```python
from pathlib import Path
from extract_pdf import extract_doc, load_manifest
from chunk_pages import chunk_page

manifest = load_manifest(Path("corpus/manifest.yaml"))
pages = extract_doc(manifest, "ebios-rm", Path("corpus/pdfs"))
chunks = chunk_page(pages[0].text, pages[0].doc_id, pages[0].page_num)
```

Run the test suite:

```
python -m pytest -q
```

## Development workflow

Two integration tests hit the real (git-ignored) ANSSI corpus and are
skipped in CI: `test_extract_pages_ad_real_pdf_strips_footer` and
`test_extract_pages_hygiene_documented_limit_current_behavior`. Their
CI-safe equivalent for the header/footer stripper is
`test_extract_pages_strips_footer_with_alternating_page_number_position`
(reportlab fixture, always runs). Contributors touching the extraction
pipeline must run the two skipped tests locally before opening a PR
and include the pytest output in the PR description — the hygiene one
is a **sentinel** that fails in both directions (regression **and**
improvement), so a fix that lowers the baseline is caught explicitly.
