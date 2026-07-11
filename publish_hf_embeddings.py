#!/usr/bin/env python3
"""Publish the public embeddings artifact to Hugging Face Hub.

Writes a **public** copy of ``corpus/embeddings_index.jsonl`` without
the ``text`` field (source text stays with ANSSI + the GitHub repo —
corpus governance, see the dataset card), then creates or updates the
dataset repository ``adriencr81/anssi-bge-m3-embeddings`` and uploads:

- ``embeddings.npy``               (the 1231×1024 float32 matrix)
- ``embeddings_index_public.jsonl`` (row-aligned metadata, no text)
- ``README.md``                     (the dataset card)

Idempotent: re-running overwrites files in place, no duplication. Uses
``huggingface_hub``; requires a WRITE token via ``hf auth login``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from huggingface_hub import HfApi

REPO_ROOT = Path(__file__).resolve().parent

EMBEDDINGS_NPY = REPO_ROOT / "corpus" / "embeddings.npy"
EMBEDDINGS_INDEX = REPO_ROOT / "corpus" / "embeddings_index.jsonl"
EMBEDDINGS_INDEX_PUBLIC = REPO_ROOT / "corpus" / "embeddings_index_public.jsonl"
DATASET_CARD = REPO_ROOT / "hf" / "dataset_card.md"

HF_REPO_ID = "adriencr81/anssi-bge-m3-embeddings"
HF_REPO_TYPE = "dataset"

# Fields kept in the public index. ``text`` is deliberately excluded —
# source text stays with ANSSI (Etalab 2.0, cyber.gouv.fr) and the
# raw chunks are available in the GitHub repo under corpus/chunks.jsonl.
PUBLIC_FIELDS = ("doc_id", "page_num", "chunk_idx", "char_start", "char_end")


def build_public_index() -> int:
    """Rewrite the index dropping the ``text`` field. Returns row count."""
    if not EMBEDDINGS_INDEX.exists():
        raise FileNotFoundError(
            f"{EMBEDDINGS_INDEX} not found — run build_embeddings.py first."
        )
    count = 0
    with EMBEDDINGS_INDEX.open("r", encoding="utf-8") as fin, \
            EMBEDDINGS_INDEX_PUBLIC.open("w", encoding="utf-8", newline="\n") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            public_row = {k: row[k] for k in PUBLIC_FIELDS}
            fout.write(json.dumps(public_row, ensure_ascii=False) + "\n")
            count += 1
    print(f"[build] wrote {EMBEDDINGS_INDEX_PUBLIC.name} — {count} rows, no text field")
    return count


def publish() -> None:
    """Create the dataset repo (idempotent) and upload the three files."""
    api = HfApi()

    print(f"[repo] ensuring dataset exists: {HF_REPO_ID}")
    api.create_repo(
        repo_id=HF_REPO_ID,
        repo_type=HF_REPO_TYPE,
        exist_ok=True,
    )

    uploads = (
        (EMBEDDINGS_NPY, "embeddings.npy"),
        (EMBEDDINGS_INDEX_PUBLIC, "embeddings_index_public.jsonl"),
        (DATASET_CARD, "README.md"),
    )
    for src, dest in uploads:
        if not src.exists():
            raise FileNotFoundError(f"missing local file: {src}")
        print(f"[upload] {src.name} -> {dest}")
        api.upload_file(
            path_or_fileobj=str(src),
            path_in_repo=dest,
            repo_id=HF_REPO_ID,
            repo_type=HF_REPO_TYPE,
        )

    print(f"[done] https://huggingface.co/datasets/{HF_REPO_ID}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish embeddings dataset to Hugging Face Hub."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only build embeddings_index_public.jsonl locally; skip upload.",
    )
    args = parser.parse_args()

    build_public_index()

    if args.dry_run:
        print("[dry-run] skipping upload")
        return 0

    publish()
    return 0


if __name__ == "__main__":
    sys.exit(main())
