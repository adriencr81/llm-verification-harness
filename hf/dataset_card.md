---
license: cc-by-4.0
language:
- fr
tags:
- retrieval
- embeddings
- rag
- ivvq
- anssi
- cybersecurity
- french
- bge-m3
pretty_name: ANSSI corpus embeddings — BGE-M3
size_categories:
- 1K<n<10K
---

# ANSSI cybersecurity guides — BGE-M3 embeddings

Semantic embeddings of 11 ANSSI (French national cybersecurity agency)
guides, produced by the open-source project
[`llm-verification-harness`](https://github.com/adriencr81/llm-verification-harness).

**Project positioning.** Transpose aerospace/defense **IVVQ**
(Integration, Verification, Validation, Qualification) practices to
non-deterministic RAG/LLM systems. The project's signature deliverable
is a *Verification Control Document* auto-generated per run (Brique 7).
This dataset is an **intermediate, machine-verifiable artifact** from
Brique 2.

## Contents

- **`embeddings.npy`** — `float32` matrix of shape `(1231, 1024)`.
  Each row = one ANSSI corpus chunk encoded with `BAAI/bge-m3`.
  L2-normalized at production time, so `matrix @ query` returns cosine
  similarity directly.
- **`embeddings_index_public.jsonl`** — 1231 JSON lines,
  **row-aligned** with `embeddings.npy` (matrix row `i` ↔ JSONL line
  `i`). Fields per row: `doc_id`, `page_num`, `chunk_idx`,
  `char_start`, `char_end`.

**Chunk source text is not included here** — see *Corpus governance*
below.

## Contract by properties, not bit-for-bit SHA256

A neural embedding is **not** bit-for-bit reproducible across machines
(BLAS/MKL versions, CPU/GPU float sum order, FP non-associativity).
Locking `embeddings.npy` SHA256 as a blocking regression would be a
*faux contrat* — it would fail on the first machine change. The GitHub
repo records the hash for traceability but tests **verifiable
properties**:

- `matrix.ndim == 2`, `matrix.shape[1] == 1024`, `dtype == float32`
- `∀i, |‖matrix[i]‖₂ − 1| < 1e-5` — the L2 contract that lets
  `matrix @ query` behave as cosine similarity
- `matrix.shape[0] == len(embeddings_index_public.jsonl)` —
  row-alignment
- Ordered subset of `chunks.jsonl` from the GitHub repo
  (`REQ-CHUNK-04` filter: chunks under 10 tokens excluded from the
  index but kept in the source `chunks.jsonl` for audit)

## Producer environment (pinned)

`embeddings.npy` was produced under this exact environment, frozen in
the repo's `corpus/manifest.yaml` under
`derived_artifacts.embeddings_npy.producer_env`:

| Component | Version |
|-----------|---------|
| model | `BAAI/bge-m3` |
| revision | `5617a9f61b028005a4858fdac845db406aefb181` |
| device | `cpu` |
| dtype | `float32` |
| dim | `1024` |
| normalize_embeddings | `true` |
| sentence-transformers | `5.6.0` |
| torch | `2.13.0` |
| transformers | `5.13.0` |
| huggingface_hub | `1.23.0` |
| numpy | `2.5.1` |

## Corpus governance — why the text is not in the index

The source corpus (11 ANSSI guides) is distributed by ANSSI on
[cyber.gouv.fr](https://cyber.gouv.fr) under *Licence Ouverte 2.0
(Etalab)*. The project respects that governance by:

- **not republishing the source text** here — the HF index carries
  identifiers only (`doc_id`, `page_num`, `chunk_idx`, `char_start`,
  `char_end`), nothing textual.
- publishing the raw chunks (with text) in the GitHub repo under
  `corpus/chunks.jsonl` — closer to source, under `git` audit.
- keeping source PDFs out of the repo (on-demand download with
  falsifiable SHA256 verification via `download_corpus.py`).

A user who wants to reconstitute the chunk text from this index can:

1. Clone the GitHub repo `adriencr81/llm-verification-harness`
2. Load `corpus/chunks.jsonl`
3. Join on `(doc_id, page_num, chunk_idx)` between the two files

The gesture **"publish the computable artifacts, respect the source
corpus governance"** is an IVVQ posture: sensitive data never rides
in the public artifacts of a verification chain.

## Reproduce `retrieve()` locally

```python
import numpy as np
import json
from sentence_transformers import SentenceTransformer
from huggingface_hub import hf_hub_download

REPO_ID = "adriencr81/anssi-bge-m3-embeddings"

npy_path = hf_hub_download(REPO_ID, "embeddings.npy", repo_type="dataset")
idx_path = hf_hub_download(REPO_ID, "embeddings_index_public.jsonl", repo_type="dataset")

matrix = np.load(npy_path, allow_pickle=False)
index = [json.loads(line) for line in open(idx_path, encoding="utf-8") if line.strip()]

model = SentenceTransformer(
    "BAAI/bge-m3",
    revision="5617a9f61b028005a4858fdac845db406aefb181",
    device="cpu",
)

q = model.encode(
    ["Quelles recommandations MFA de l'ANSSI ?"],
    normalize_embeddings=True,
)[0].astype(np.float32)

scores = matrix @ q
top4 = np.argsort(-scores)[:4]
for i in top4:
    print(f"{scores[i]:.4f}", index[i])
```

To also get the chunk `text` field for each hit, use `retrieve.py` in
the GitHub repo — it joins the same matrix with `corpus/chunks.jsonl`
locally.

## Downstream use

Consumed by the RAG pipeline in the same project
([`ask.py`](https://github.com/adriencr81/llm-verification-harness/blob/main/ask.py),
Brique 3): question → retrieval on this matrix → LLM call →
citation-parsed answer. The Brique 7 Verification Control Document
will report bench results against these embeddings.

## License

- **Embeddings and public index** (this HF dataset): `CC-BY-4.0` —
  attribution required.
- **Source corpus text** (not redistributed here): remains under
  ANSSI's *Licence Ouverte 2.0 (Etalab)*.

## Cite

```
Deleuil, A. (2026). llm-verification-harness — Applying aerospace IVVQ
to RAG/LLM systems. https://github.com/adriencr81/llm-verification-harness
```

## Contact

Adrien Deleuil — [huggingface.co/adriencr81](https://huggingface.co/adriencr81)
