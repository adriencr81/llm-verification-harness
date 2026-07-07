"""Tests for extract_all.py.

Two layers:

1. **Unit tests** — drive :func:`extract_all.extract_all` on
   deterministic reportlab fixtures + a tmp manifest. Cover the
   contract of the persisted artifact (line count, key order, UTF-8,
   1-indexed page_num per doc, manifest order, atomic write,
   idempotence, error aggregation).
2. **Baseline tests on ``corpus/pages.jsonl``** — always run (the file
   is versioned, so it's present on a fresh clone). Verify the
   committed baseline matches the manifest declarations: doc_id
   inventory, per-doc page counts, monotone page_num sequences,
   JSONL well-formedness. Concretely materializes REQ-CORPUS-04 : any
   silent drift in the extractor output surfaces as either a diff on
   ``pages.jsonl`` (caught by ``git diff``) or a broken invariant here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from extract_all import (
    ExtractionReport,
    PAGES_JSONL_PATH,
    extract_all,
    page_to_json_line,
)
from extract_pdf import Page, load_manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "corpus" / "manifest.yaml"


def _make_pdf(path: Path, pages: list[list[str]]) -> None:
    """Create a fixture PDF where each inner list is one page's lines."""
    c = canvas.Canvas(str(path), pagesize=A4)
    _, height = A4
    for lines in pages:
        y = height - 60
        for line in lines:
            c.drawString(60, y, line)
            y -= 16
        c.showPage()
    c.save()


def _write_fixture_manifest(
    manifest_path: Path, entries: list[dict]
) -> dict:
    """Write a minimal manifest.yaml with the given entries and return it parsed."""
    payload = {"schema_version": 1, "documents": entries}
    manifest_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return payload


# --- page_to_json_line : serialization contract ---


def test_page_to_json_line_has_fixed_key_order():
    page = Page(doc_id="foo", page_num=3, text="hello")
    line = page_to_json_line(page)
    # doc_id must precede page_num must precede text — baseline stability
    assert line.index('"doc_id"') < line.index('"page_num"') < line.index('"text"')


def test_page_to_json_line_preserves_utf8_natively():
    """ensure_ascii=False keeps French accents readable in git diff.

    A regression here (someone flips to ensure_ascii=True) would inflate
    the baseline by ~2x and make diffs unreadable — worth pinning.
    """
    page = Page(doc_id="fr", page_num=1, text="préréquis — événements")
    line = page_to_json_line(page)
    assert "préréquis" in line
    assert "événements" in line
    assert "\\u" not in line


def test_page_to_json_line_escapes_newlines_within_text():
    """Internal newlines must be escaped, or JSONL invariant breaks."""
    page = Page(doc_id="d", page_num=1, text="line1\nline2")
    line = page_to_json_line(page)
    assert "\n" not in line  # no raw newline in the emitted line
    assert "\\n" in line     # escaped one is present
    # Roundtrip
    assert json.loads(line)["text"] == "line1\nline2"


# --- extract_all : write contract ---


def _build_two_doc_fixture(tmp_path: Path) -> tuple[dict, Path, Path]:
    """Set up a manifest with 2 fixture docs, return (manifest, pdf_dir, out)."""
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    # Distinct words per page: digit-normalization ("A1"→"a#") would
    # otherwise collapse "contenu A1/A2/A3" into a single normalized
    # line, tripping the noise-stripper (100% repetition -> empty).
    _make_pdf(pdf_dir / "a.pdf", [["alpha uno"], ["bravo duo"], ["charlie tres"]])
    _make_pdf(pdf_dir / "b.pdf", [["delta primero"], ["echo segundo"]])
    manifest_path = tmp_path / "manifest.yaml"
    manifest = _write_fixture_manifest(
        manifest_path,
        [
            {"doc_id": "doc-a", "filename": "a.pdf", "pages": 3},
            {"doc_id": "doc-b", "filename": "b.pdf", "pages": 2},
        ],
    )
    return manifest, pdf_dir, tmp_path / "pages.jsonl"


def test_extract_all_writes_one_line_per_page(tmp_path):
    manifest, pdf_dir, out = _build_two_doc_fixture(tmp_path)
    report = extract_all(manifest, pdf_dir, out)
    assert report.ok
    assert report.docs_extracted == 2
    assert report.pages_written == 5
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5


def test_extract_all_preserves_manifest_document_order(tmp_path):
    """First doc in manifest must be first block in output — VCD needs
    a deterministic ordering to cite ``pages.jsonl`` slices.

    Uses ``zeta`` before ``alpha`` in the manifest so a regression
    that ``sorted()``s docs (alphabetic) would swap them and fail the
    assertion — the previous ``doc-a``/``doc-b`` fixture was already
    alphabetic and would have passed such a regression silently.
    """
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _make_pdf(pdf_dir / "zeta.pdf", [["gamma tres"], ["delta cuatro"]])
    _make_pdf(pdf_dir / "alpha.pdf", [["alpha uno"], ["bravo duo"]])
    manifest_path = tmp_path / "manifest.yaml"
    manifest = _write_fixture_manifest(
        manifest_path,
        [
            {"doc_id": "zeta", "filename": "zeta.pdf", "pages": 2},
            {"doc_id": "alpha", "filename": "alpha.pdf", "pages": 2},
        ],
    )
    out = tmp_path / "pages.jsonl"
    extract_all(manifest, pdf_dir, out)
    records = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    doc_id_sequence = [r["doc_id"] for r in records]
    # zeta first (manifest order), then alpha — a sorted() regression
    # would produce ["alpha"] * 2 + ["zeta"] * 2 and fail this.
    assert doc_id_sequence == ["zeta"] * 2 + ["alpha"] * 2


def test_extract_all_emits_1_indexed_monotone_page_num_per_doc(tmp_path):
    manifest, pdf_dir, out = _build_two_doc_fixture(tmp_path)
    extract_all(manifest, pdf_dir, out)
    records = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    by_doc: dict[str, list[int]] = {}
    for r in records:
        by_doc.setdefault(r["doc_id"], []).append(r["page_num"])
    assert by_doc["doc-a"] == [1, 2, 3]
    assert by_doc["doc-b"] == [1, 2]


def test_extract_all_second_run_is_bit_for_bit_identical(tmp_path):
    """Two runs on the same fixture must produce byte-identical files.

    Any hidden nondeterminism (dict ordering, timestamps, hash
    salt) would silently poison the committed baseline — pinned here.
    """
    manifest, pdf_dir, out = _build_two_doc_fixture(tmp_path)
    extract_all(manifest, pdf_dir, out)
    first = out.read_bytes()
    extract_all(manifest, pdf_dir, out)
    second = out.read_bytes()
    assert first == second


# --- extract_all : failure handling ---


def test_extract_all_aggregates_failures_across_docs(tmp_path):
    """One broken doc must not stop extraction of the others — a full
    inventory of violations is more useful than a fail-fast trace."""
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _make_pdf(pdf_dir / "good.pdf", [["A"], ["B"]])
    # "bad.pdf" intentionally missing on disk
    manifest_path = tmp_path / "manifest.yaml"
    manifest = _write_fixture_manifest(
        manifest_path,
        [
            {"doc_id": "good", "filename": "good.pdf", "pages": 2},
            {"doc_id": "bad", "filename": "bad.pdf", "pages": 2},
        ],
    )
    out = tmp_path / "pages.jsonl"
    report = extract_all(manifest, pdf_dir, out)
    assert not report.ok
    assert len(report.errors) == 1
    assert "bad" in report.errors[0]


def test_extract_all_does_not_write_output_on_any_failure(tmp_path):
    """Atomic-write invariant: a partial ``pages.jsonl`` is worse than
    nothing (chunker would consume a truncated baseline). Sidecar
    idiom must clean up on error."""
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _make_pdf(pdf_dir / "good.pdf", [["A"]])
    manifest_path = tmp_path / "manifest.yaml"
    manifest = _write_fixture_manifest(
        manifest_path,
        [
            {"doc_id": "good", "filename": "good.pdf", "pages": 1},
            {"doc_id": "bad", "filename": "missing.pdf", "pages": 1},
        ],
    )
    out = tmp_path / "pages.jsonl"
    report = extract_all(manifest, pdf_dir, out)
    assert not report.ok
    assert not out.exists()
    assert not out.with_name(out.name + ".tmp").exists()


def test_extract_all_report_type_carries_ok_property(tmp_path):
    """The ``ok`` boolean is the single check callers rely on."""
    report = ExtractionReport()
    assert report.ok
    report.errors.append("x")
    assert not report.ok


# --- Baseline : the committed corpus/pages.jsonl ---


def _load_baseline() -> list[dict]:
    """Read the committed corpus/pages.jsonl.

    Fails loudly if absent: the file is versioned, so a missing
    baseline on a fresh clone means the extraction pipeline was
    broken during a merge, not a normal state to skip over.

    Read discipline:
    - ``newline=""`` disables universal-newline translation. Without
      it, a CRLF-checked-out file would be silently normalized to LF
      on read and a broken EOL state would pass tests.
    - ``split("\\n")`` — matches the writer (``handle.write("\\n")``)
      and refuses the 7 extra Unicode line separators that
      ``str.splitlines()`` would swallow (U+2028, U+2029, etc.) — any
      of those inside a French page's text would silently inflate the
      record count.
    """
    if not PAGES_JSONL_PATH.exists():
        pytest.fail(
            f"corpus/pages.jsonl missing at {PAGES_JSONL_PATH}. "
            "The baseline is versioned — regenerate with "
            "`python extract_all.py` and commit."
        )
    content = PAGES_JSONL_PATH.read_text(encoding="utf-8", newline="")
    return [json.loads(line) for line in content.split("\n") if line]


def test_baseline_is_valid_jsonl_with_expected_keys():
    records = _load_baseline()
    assert records, "baseline is empty"
    for rec in records:
        assert set(rec.keys()) == {"doc_id", "page_num", "text"}
        assert isinstance(rec["doc_id"], str)
        assert isinstance(rec["page_num"], int)
        assert isinstance(rec["text"], str)


def test_baseline_covers_every_document_in_manifest():
    manifest = load_manifest(MANIFEST_PATH)
    manifest_ids = {doc["doc_id"] for doc in manifest["documents"]}
    records = _load_baseline()
    baseline_ids = {r["doc_id"] for r in records}
    assert baseline_ids == manifest_ids, (
        f"drift between manifest and baseline: "
        f"missing={manifest_ids - baseline_ids}, "
        f"extra={baseline_ids - manifest_ids}"
    )


def test_baseline_page_count_per_doc_matches_manifest():
    """The per-doc page count in the baseline must equal
    ``manifest.pages`` — direct materialization of REQ-CORPUS-02
    persisted at rest."""
    manifest = load_manifest(MANIFEST_PATH)
    expected = {doc["doc_id"]: doc["pages"] for doc in manifest["documents"]}
    records = _load_baseline()
    actual: dict[str, int] = {}
    for r in records:
        actual[r["doc_id"]] = actual.get(r["doc_id"], 0) + 1
    assert actual == expected


def test_baseline_page_num_is_1_indexed_and_contiguous_per_doc():
    records = _load_baseline()
    by_doc: dict[str, list[int]] = {}
    for r in records:
        by_doc.setdefault(r["doc_id"], []).append(r["page_num"])
    for doc_id, page_nums in by_doc.items():
        expected = list(range(1, len(page_nums) + 1))
        assert page_nums == expected, (
            f"[{doc_id}] page_num sequence broken: got {page_nums[:5]}..."
        )


def test_baseline_hash_matches_manifest():
    """Bit-for-bit contract, machine-enforced.

    Mirrors REQ-CORPUS-01 (SHA256 on PDFs) on the derived baseline.
    Any drift — including a *text-only* drift that leaves counts and
    ordering intact — fails here without depending on a human running
    ``git diff``. Materializes REQ-CORPUS-04 at CI level.
    """
    manifest = load_manifest(MANIFEST_PATH)
    declared = manifest["derived_artifacts"]["pages_jsonl"]["sha256"]
    actual = hashlib.sha256(PAGES_JSONL_PATH.read_bytes()).hexdigest()
    assert actual == declared, (
        f"corpus/pages.jsonl SHA256 mismatch — baseline drift.\n"
        f"  declared: {declared}\n"
        f"  actual:   {actual}\n"
        f"  Either restore the baseline or regenerate deliberately "
        f"(python extract_all.py) and bump derived_artifacts."
        f"pages_jsonl.sha256 in corpus/manifest.yaml."
    )


def test_baseline_uses_lf_line_endings_only():
    """The bit-for-bit contract is platform-invariant.

    A CR byte anywhere in the on-disk file would mean either a
    Windows checkout with CRLF translation active (`.gitattributes`
    misconfigured / absent) or a writer that stopped forcing
    ``newline="\\n"``. Either breaks the SHA256 contract silently.
    """
    raw = PAGES_JSONL_PATH.read_bytes()
    assert b"\r" not in raw, (
        "CR byte found in corpus/pages.jsonl — likely a CRLF checkout "
        "on Windows. Verify .gitattributes pins `eol=lf` on this file."
    )


def test_baseline_preserves_manifest_document_order():
    """Documents in the baseline must appear in the same order as in
    ``manifest.yaml`` — the VCD (Brique 7) cites baseline byte offsets
    to prove non-alteration; a reorder would silently invalidate every
    citation."""
    manifest = load_manifest(MANIFEST_PATH)
    manifest_order = [doc["doc_id"] for doc in manifest["documents"]]
    records = _load_baseline()
    # Extract first-occurrence order
    seen: list[str] = []
    for r in records:
        if r["doc_id"] not in seen:
            seen.append(r["doc_id"])
    assert seen == manifest_order
