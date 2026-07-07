#!/usr/bin/env python3
"""Persist the full corpus extraction to ``corpus/pages.jsonl``.

Pipeline stage: reads ``corpus/manifest.yaml``, calls
:func:`extract_pdf.extract_doc` on each declared document, and streams the
resulting :class:`~extract_pdf.Page` records — one JSON object per line —
into ``corpus/pages.jsonl``. The chunking stage consumes this file, not
pdfplumber, so PDF parsing is paid once and the extracted text becomes
independently auditable.

Design intent
-------------
- **Two-stage pipeline (persist between extraction and chunking)** — the
  chunker must not re-open PDFs. A single artifact on disk means:
  extraction regressions are detectable by ``git diff`` on
  ``pages.jsonl``; the ``guide-hygiene.pdf`` documented limit stays
  inspectable at rest; chunking has a stable byte-for-byte input.
- **Committed baseline** — ``corpus/pages.jsonl`` is versioned. The
  contract is the same as the manifest SHA256: a silent drift is refused
  by construction; a deliberate bump requires a PR. Materializes
  REQ-CORPUS-04.
- **Deterministic output** — manifest order for documents, 1-indexed
  ``page_num`` for pages, JSON keys emitted in ``(doc_id, page_num, text)``
  order, ``ensure_ascii=False`` so French text stays readable in ``git
  diff``. Two runs on the same corpus must produce byte-identical files.
- **Atomic write via ``.tmp`` sidecar** — same idiom as
  ``download_corpus.fetch``. A crash mid-run leaves the previous
  ``pages.jsonl`` intact; downstream never observes a truncated JSONL.

Upstream requirements (see ``docs/REQUIREMENTS.md``)
---------------------------------------------------
- **REQ-CORPUS-02** — Page-count invariant. Enforced transitively:
  :func:`extract_pdf.extract_doc` refuses to emit Pages if the actual
  count diverges from the manifest, so any line written here already
  satisfies ``page_num <= manifest.pages(doc_id)``.
- **REQ-CORPUS-04** — Persisted extraction baseline. Materialized by
  the committed ``corpus/pages.jsonl``. Bit-for-bit regression
  detectable via ``git diff``.

Exit codes
----------
``0`` on success (file written or already up-to-date), ``1`` on any
extraction failure (unknown doc_id, missing PDF, page-count mismatch,
empty extraction). Errors are aggregated per document rather than
fail-fast, so a single run inventories every violation.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from extract_pdf import (
    ExtractionError,
    Page,
    extract_doc,
    load_manifest,
)

REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = REPO_ROOT / "corpus" / "manifest.yaml"
PDF_DIR = REPO_ROOT / "corpus" / "pdfs"
PAGES_JSONL_PATH = REPO_ROOT / "corpus" / "pages.jsonl"


@dataclass
class ExtractionReport:
    """Aggregated result of a corpus-wide extraction pass.

    ``.ok`` is the single boolean the caller checks to decide whether the
    write happened; ``errors`` carries human-readable messages for
    logging and VCD evidence.
    """

    docs_extracted: int = 0
    pages_written: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def page_to_json_line(page: Page) -> str:
    """Serialize a :class:`Page` to a single JSONL line.

    Field order is fixed (``doc_id``, ``page_num``, ``text``) so the
    committed baseline is stable across Python versions — ``json.dumps``
    preserves dict insertion order since 3.7. ``ensure_ascii=False``
    keeps French accents readable in ``git diff``; internal newlines in
    ``text`` are escaped as ``\\n`` by ``json.dumps``, preserving the
    one-record-per-line JSONL invariant.
    """
    payload = {
        "doc_id": page.doc_id,
        "page_num": page.page_num,
        "text": page.text,
    }
    return json.dumps(payload, ensure_ascii=False)


def extract_all(
    manifest: dict, pdf_dir: Path, output_path: Path
) -> ExtractionReport:
    """Extract every declared document and stream results to ``output_path``.

    Iterates ``manifest["documents"]`` in declared order, calling
    :func:`extract_pdf.extract_doc` for each entry. Pages are streamed to
    a ``.tmp`` sidecar to keep memory bounded on larger corpora, then
    atomically renamed on success. Any :class:`ExtractionError` is
    recorded in the report and the next document is attempted — a
    partial report is less useful than a full inventory of violations.

    The sidecar is deleted on any failure, so an aborted run never
    leaves a half-written ``pages.jsonl`` on disk.
    """
    report = ExtractionReport()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".tmp")

    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
            for doc in manifest["documents"]:
                doc_id = doc["doc_id"]
                try:
                    pages = extract_doc(manifest, doc_id, pdf_dir)
                except ExtractionError as err:
                    report.errors.append(str(err))
                    print(f"[{doc_id}] FAIL — {err.__class__.__name__}", file=sys.stderr)
                    continue

                for page in pages:
                    handle.write(page_to_json_line(page))
                    handle.write("\n")

                report.docs_extracted += 1
                report.pages_written += len(pages)
                print(f"[{doc_id}] OK ({len(pages)} page(s))")

        if report.ok:
            tmp_path.replace(output_path)
        else:
            tmp_path.unlink(missing_ok=True)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    return report


def main() -> int:
    manifest = load_manifest(MANIFEST_PATH)
    documents = manifest["documents"]
    print(
        f"Extracting {len(documents)} document(s) from {PDF_DIR} "
        f"-> {PAGES_JSONL_PATH}"
    )

    report = extract_all(manifest, PDF_DIR, PAGES_JSONL_PATH)

    if report.errors:
        print("\n=== EXTRACTION FAILURES ===", file=sys.stderr)
        for err in report.errors:
            print(err, file=sys.stderr)
        return 1

    print(
        f"\nExtracted {report.docs_extracted} document(s), "
        f"{report.pages_written} page(s) -> {PAGES_JSONL_PATH}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
