"""Extract ANSSI PDF corpus into logical Pages, one per PDF page.

Pipeline stage: takes a ``doc_id`` whose PDF has been validated by
``download_corpus.py`` (SHA256 contract enforced), returns a
``list[Page]`` consumed by the chunking stage (next lot of Brique 1).

Design intent
-------------
- ``extract_text(use_text_flow=True)`` — the only pdfplumber mode
  producing readable text on both standard ANSSI layouts (single-column,
  A4) and the pathological ``guide-hygiene.pdf`` custom rendering
  (glyphs geometrically scattered across 3 Y-buckets per page).
  ``extract_text()`` alone scrambles the reading order; ``layout=True``
  emits mostly-empty padded lines and drops content.
- Header/footer stripping by **repetition** (fraction ≥ 0.5 of pages)
  rather than geometry: ``guide-hygiene.pdf`` has no exploitable Y
  structure, so a crop-based approach is a dead end. The repetition
  heuristic handles 10/11 PDFs cleanly; the guide-hygiene case degrades
  gracefully (its header/footer is fused with page-1 body content and
  is preserved as a documented limit).
- Digits normalized to ``#`` so that per-page numbers (``14``, ``15``,
  ``16``…) do not defeat the noise detector — every page number would
  otherwise appear "unique" and slip through.

Upstream requirements (see ``docs/REQUIREMENTS.md``) :
- REQ-CORPUS-02 : provenance ``(doc_id, page_num)`` with invariant
  ``page_num ∈ [1, manifest.pages(doc_id)]``. Enforced here when the
  manifest's ``pages`` field is passed via ``expected_page_count`` — a
  divergence raises :class:`PageCountMismatchError`. Reaches "enforced"
  status when consumed by the chunking stage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
import yaml

from download_corpus import CorpusError


class ExtractionError(RuntimeError):
    """Base class for any failure raised by the PDF extraction pipeline.

    Consumers downstream (chunking, VCD) can catch this single type to
    react to "any extraction contract violation" without enumerating
    every subclass. Distinct from
    :class:`download_corpus.CorpusError` on purpose: corpus errors are
    about the input contract (SHA256, size), extraction errors are
    about the transformation.
    """


class UnknownDocIdError(ExtractionError):
    """The requested ``doc_id`` is not declared in the manifest."""


class PdfMissingError(ExtractionError):
    """The physical PDF file is absent on disk.

    Semantically distinct from
    :class:`download_corpus.MissingSourceError` — that one is raised by
    the corpus contract enforcer before extraction is even attempted.
    Reaching this class means the caller skipped the corpus verification
    step, which is a misuse worth surfacing separately.
    """


class EmptyExtractionError(ExtractionError):
    """Extraction produced zero characters across all pages.

    Typical cause: scanned PDF with no embedded text layer. Surfaced
    rather than silenced so that the caller can decide (OCR pipeline?
    skip?) — a silent empty ``list[Page]`` would poison the chunk
    store.
    """


class PageCountMismatchError(CorpusError, ExtractionError):
    """The PDF's actual page count diverges from the manifest declaration.

    Materializes REQ-CORPUS-02: chunks carry ``(doc_id, page_num)``
    provenance whose ``page_num`` must lie in ``[1, manifest.pages]``.
    If the PDF changes page count without a manifest bump, the whole
    downstream provenance is silently miscalibrated — refused here.

    Multiple inheritance is deliberate. This condition is *both* a
    violation of the corpus contract (the file on disk is not the one
    the manifest froze) *and* a failure of the extraction pipeline
    (transformation refused). A VCD consumer catching
    :class:`~download_corpus.CorpusError` to enumerate corpus contract
    violations must not miss this; a pipeline caller catching
    :class:`ExtractionError` to react to any extraction failure must
    not miss it either. MRO resolves left-to-right so ``CorpusError``
    is preferred when both apply symmetrically.
    """


@dataclass(frozen=True)
class Page:
    """One PDF page after header/footer stripping.

    ``doc_id`` is the stable slug (see ``corpus/manifest.yaml``
    § Identité stable) — the join key toward the manifest, not the
    filename.

    ``page_num`` is 1-indexed to match printed page numbers and the
    manifest's ``pages`` count.

    ``text`` is the extracted text with lines repeated on ≥ 50% of
    pages removed. May be empty for pages that were entirely
    boilerplate (fully repeated header/footer with no body).
    """

    doc_id: str
    page_num: int
    text: str


# Fraction of pages on which a normalized line must appear to be
# treated as header/footer boilerplate. Tuned empirically on the ANSSI
# corpus: 0.5 catches the guide-wide footer ("RECOMMANDATIONS…") without
# swallowing recurring paragraphs like "Objectif" (which appears on
# fewer than half the pages in every guide we've inspected).
NOISE_THRESHOLD = 0.5

# Below this many pages the repetition heuristic is unreliable (a
# unique line on page 1 of a 2-page doc would count as 50% and be
# stripped). Small docs — cover leaflets, appendices — keep every line.
MIN_PAGES_FOR_STRIP = 3

_DIGIT_RE = re.compile(r"\d+")
# Chars to collapse to a single space (whitespace + every dash variant
# ANSSI uses in footers : "16– RECO…" vs "…DIRECTORY–17").
_WS_DASH_RE = re.compile(r"[\s\-–—]+")
# Chars to strip from the ends after normalization. The page number
# marker ``#`` migrates between the start and end of the footer line
# on even/odd pages ("16– RECO…" vs "…DIRECTORY –17") — without this
# trim the two variants hash differently and each falls under the 50%
# threshold, defeating the whole detector.
_EDGE_STRIP = "# "
# A line made only of digits, whitespace and dash-like punctuation is
# almost certainly a standalone page number ("14", "14–17", " 14 ").
# Always noise, cheap to short-circuit before hashing — and the
# empty-string key produced by :func:`_normalize_line` on such a line
# would otherwise collide with truly blank lines, which we don't want
# to remove from the middle of pages.
_DIGIT_ONLY_RE = re.compile(r"^[\s\d\-–—.]+$")


def _normalize_line(line: str) -> str:
    """Collapse per-page differences so that the same header/footer
    template hashes identically across pages.

    Steps:
    1. Lower-case (defeats "GUIDE" vs "Guide").
    2. Replace every digit run with ``#`` (defeats page numbers).
    3. Collapse whitespace and dashes into a single space (defeats
       "16– RECO" vs "16 - RECO" vs "16—RECO").
    4. Strip ``#`` and spaces from the ends (defeats the page-number
       marker migrating between line start and end).

    Middle-of-line ``#`` markers are preserved so that "page # de #"
    still differs from "page # of X".
    """
    s = _DIGIT_RE.sub("#", line.lower())
    s = _WS_DASH_RE.sub(" ", s)
    return s.strip(_EDGE_STRIP)


def _is_page_number_only(line: str) -> bool:
    """The line contains only digits, whitespace, and dash-like punctuation.

    Treated as noise unconditionally — standalone page numbers appear
    on virtually every page and carry no semantic content downstream
    (the ``page_num`` field on :class:`Page` already conveys the info).
    """
    return bool(line.strip()) and bool(_DIGIT_ONLY_RE.match(line))


def _strip_noise(pages_text: list[str]) -> list[str]:
    """Remove header/footer boilerplate from every page.

    Two-pass strategy:

    1. **Page-number-only** lines (``"14"``, ``"14–17"``) are dropped
       from every page unconditionally. Their normalized hash is empty,
       so they cannot be caught by the repetition pass without being
       conflated with blank content lines.
    2. **Repetition-based** stripping: any normalized line appearing on
       ≥ ``NOISE_THRESHOLD`` of pages is removed from every page it
       occurs on. Intra-page repetition counts as one occurrence — the
       signal is cross-page recurrence, not intra-page.

    Returns the input unchanged when the doc has fewer than
    ``MIN_PAGES_FOR_STRIP`` pages (safeguard against over-stripping
    tiny fixtures / short leaflets).
    """
    n = len(pages_text)
    if n < MIN_PAGES_FOR_STRIP:
        return pages_text

    counts: dict[str, int] = {}
    for text in pages_text:
        seen_on_page: set[str] = set()
        for line in text.split("\n"):
            if _is_page_number_only(line):
                continue
            key = _normalize_line(line)
            if not key or key in seen_on_page:
                continue
            seen_on_page.add(key)
            counts[key] = counts.get(key, 0) + 1

    noise = {key for key, c in counts.items() if c / n >= NOISE_THRESHOLD}

    return [
        "\n".join(
            line
            for line in text.split("\n")
            if not _is_page_number_only(line)
            and _normalize_line(line) not in noise
        )
        for text in pages_text
    ]


def extract_pages(
    pdf_path: Path,
    doc_id: str,
    *,
    expected_page_count: int | None = None,
) -> list[Page]:
    """Extract logical Pages from a PDF.

    Uses ``extract_text(use_text_flow=True)`` — the only mode producing
    readable text on both standard ANSSI layouts and the pathological
    ``guide-hygiene.pdf`` custom rendering.

    Header/footer lines detected by repetition (≥ ``NOISE_THRESHOLD``
    of pages) are stripped. Known limit on ``guide-hygiene.pdf``: its
    header text is fused with the body on the same first line (single
    Y-bucket for the whole page), so the repetition detector cannot
    isolate it — the boilerplate is preserved as-is on that doc. Not a
    regression; a documented gap to be closed by a targeted regex pass
    if it moves the needle on retrieval quality (Brique 2 evaluation).

    Args:
        pdf_path: Filesystem path to the PDF.
        doc_id: Stable slug identifying the document (join key toward
            the manifest and downstream chunks).
        expected_page_count: If set, the actual page count must match
            exactly — materializes REQ-CORPUS-02. Typically sourced
            from ``manifest.yaml``'s ``pages`` field via
            :func:`extract_doc`.

    Raises:
        PdfMissingError: ``pdf_path`` does not exist on disk.
        PageCountMismatchError: ``expected_page_count`` set and
            disagrees with actual — REQ-CORPUS-02 violated.
        EmptyExtractionError: Zero characters extracted across every
            page (likely a scanned PDF without a text layer).
    """
    if not pdf_path.exists():
        raise PdfMissingError(
            f"[{doc_id}] PDF absent: {pdf_path}\n"
            f"  Run download_corpus.py to fetch it, or restore the file."
        )

    with pdfplumber.open(pdf_path) as pdf:
        raw_texts = [
            (page.extract_text(use_text_flow=True) or "") for page in pdf.pages
        ]

    actual_n = len(raw_texts)
    if expected_page_count is not None and actual_n != expected_page_count:
        raise PageCountMismatchError(
            f"[{doc_id}] page count mismatch — manifest declares "
            f"{expected_page_count}, PDF has {actual_n}. "
            f"REQ-CORPUS-02 violated.\n"
            f"  Either restore the source PDF or bump the manifest "
            f"(pages + sha256) deliberately."
        )

    cleaned = _strip_noise(raw_texts)

    if sum(len(t) for t in cleaned) == 0:
        raise EmptyExtractionError(
            f"[{doc_id}] extraction produced zero characters across "
            f"{actual_n} page(s) — scanned PDF without text layer, or "
            f"extraction failure? Refuse to return empty pages silently."
        )

    return [
        Page(doc_id=doc_id, page_num=i + 1, text=text)
        for i, text in enumerate(cleaned)
    ]


def load_manifest(manifest_path: Path) -> dict:
    """Read and parse ``corpus/manifest.yaml``.

    Exposed as a standalone helper so consumers looping over multiple
    documents (chunking stage, VCD generator) pay the YAML load cost
    once, then pass the resulting dict to :func:`extract_doc` per
    document. Prior signature took the path and reloaded on every
    call — corrected before the chunking stage freezes the interface.
    """
    with manifest_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def extract_doc(manifest: dict, doc_id: str, pdf_dir: Path) -> list[Page]:
    """Manifest-aware wrapper: resolve ``doc_id`` in an already-loaded
    manifest dict, then extract.

    Finds the entry whose ``doc_id`` matches, resolves its PDF under
    ``pdf_dir`` and forwards the extraction — passing the manifest's
    ``pages`` field as ``expected_page_count`` so REQ-CORPUS-02 is
    enforced through this path.

    Args:
        manifest: Parsed manifest dict, typically from
            :func:`load_manifest`. Must contain a ``"documents"`` list
            whose entries carry ``doc_id`` / ``filename`` / ``pages``.
        doc_id: Stable slug of the document to extract.
        pdf_dir: Directory containing the physical PDFs.

    Raises:
        UnknownDocIdError: ``doc_id`` is not declared in the manifest.
        (plus any exception raised by :func:`extract_pages`).
    """
    for doc in manifest["documents"]:
        if doc["doc_id"] == doc_id:
            return extract_pages(
                pdf_dir / doc["filename"],
                doc_id,
                expected_page_count=doc.get("pages"),
            )

    known = ", ".join(sorted(d["doc_id"] for d in manifest["documents"]))
    raise UnknownDocIdError(
        f"doc_id={doc_id!r} not declared in manifest.\n"
        f"  Known: {known}"
    )
