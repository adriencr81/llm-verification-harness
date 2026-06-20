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
- [ ] Brique 1 — Document ingestion, chunking, provenance tracking
- [ ] Brique 2 — Embeddings and semantic retrieval (cosine similarity)
- [ ] Brique 3 — Full RAG pipeline (question → retrieve → generate)
- [ ] Brique 4 — OWASP LLM01 (indirect prompt injection) test
- [ ] Brique 5 — IVVQ-style test case formalization (YAML runner)
- [ ] Brique 6 — Leak (LLM02), faithfulness (LLM09, LLM-as-judge), drift
- [ ] Brique 7 — Auto-generated Verification Control Document
- [ ] Brique 8 — Catalog (OWASP/ATLAS), unit tests, CI, polished README
- [ ] Brique 9 — Hardening loop (detect → fix → re-verify on injection)
- [ ] Brique 10 — Red/Blue agentic duel on the RAG pipeline

## Stack

Python 3.13, OpenAI SDK via OpenRouter (Claude Sonnet 4.6 as primary LLM,
provider-agnostic by design).

## Status

Brique 0 complete. Active development.