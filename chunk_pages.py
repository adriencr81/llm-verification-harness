#!/usr/bin/env python3
"""Chunk ``corpus/pages.jsonl`` into ``corpus/chunks.jsonl`` with provenance.

Pipeline stage: reads the persisted extraction baseline
(``corpus/pages.jsonl``, source of truth — pdfplumber is NEVER re-opened
here), produces ``list[Chunk]`` with strict-substring provenance into
the source page. The embedding stage (Brique 2) consumes this file.

Design intent
-------------
- **Recursive character splitter, position-aware.** Cascade of
  separators ``["\\n\\n", "\\n", ". ", " ", ""]``, deepening only when
  the current level leaves a segment above ``MAX_TOKENS``. Same
  algorithm as LangChain's ``RecursiveCharacterTextSplitter`` in
  spirit, but implemented in ~120 lines with zero external dependency
  beyond ``tiktoken``, so the chunker is auditable end-to-end for VCD
  Brique 7. Separators glue to the LEFT piece: contiguous atoms cover
  the source text with no gaps, so any concatenation of adjacent atoms
  is a strict substring of the page.
- **Tokenizer = ``cl100k_base`` (tiktoken).** Explicit choice, documented
  in ``derived_artifacts.chunks_jsonl.producer_env``:
  * Independent of the eventual embedding model (Brique 2 will bench
    several — mistral-embed, BGE-M3, etc.). Coupling the chunk boundary
    to a specific embedder's tokenizer would force a re-chunk on every
    B2 iteration, cascading SHA256 across ``pages.jsonl`` /
    ``chunks.jsonl`` for no benefit.
  * cl100k over-tokenizes French vs a native-FR tokenizer (~1.5-2×),
    so "500 tokens cl100k" ≈ "300 tokens BGE". Kept intentionally as a
    "proxy for textual complexity" that is stable, versionable, and
    testable in CI without downloading an embedding model.
  * Swappable via ``--tokenizer`` flag if B2 evaluation reveals a
    material biais on retrieval quality.
- **Never cross-page.** ``chunk_page`` is the atomic unit; ``chunk_all``
  loops pages and concatenates the per-page chunk lists. Because
  ``page_num`` is copied verbatim from the ``Page`` record into every
  emitted ``Chunk``, and ``extract_pdf.extract_doc`` already guarantees
  ``page_num ∈ [1, manifest.pages]`` (REQ-CORPUS-02 at Page boundary),
  the chunk-side invariant ``chunk.page_num ∈ [1, manifest.pages]``
  holds by construction — no chunk-side re-check needed, no
  ``pages.jsonl`` re-read, no PDF re-open.
- **Strict-substring provenance.** For every emitted chunk,
  ``page.text[chunk.char_start:chunk.char_end] == chunk.text``. This is
  the falsifiability contract of REQ-CHUNK-02 — a VCD consumer at
  Brique 7 can, given a chunk_id, load ``pages.jsonl`` and verify
  citation authenticity without any pdfplumber dependency.
- **Overlap = 75 tokens (~15%).** Insurance against the classic RAG
  bug where a semantic unit (an ANSSI recommendation) is bisected
  between two chunks and appears complete in neither. Overlap
  materializes as overlapping ``[char_start, char_end)`` windows into
  the SAME page — never across pages. Duplication cost in the embedding
  store: ~15%, negligible for 833 pages.
- **Determinism.** For a fixed ``pages.jsonl``, fixed tiktoken version,
  and fixed constants, two runs produce byte-identical ``chunks.jsonl``:
  document order = manifest order (via source file order), pages
  1-indexed per doc, ``chunk_idx`` 0-indexed per page, JSON keys in
  fixed order, ``ensure_ascii=False``, LF pinned by ``.gitattributes``.
- **Atomic write via ``.tmp`` sidecar.** Same idiom as
  ``download_corpus.fetch`` and ``extract_all.extract_all`` — a crash
  mid-run leaves the previous ``chunks.jsonl`` intact.

Upstream requirements (see ``docs/REQUIREMENTS.md``)
---------------------------------------------------
- **REQ-CORPUS-02** — Provenance ``(doc_id, page_num)`` with
  ``page_num ∈ [1, manifest.pages]``. Reaches *fully enforced* here:
  every chunk carries ``page_num`` inherited byte-for-byte from a Page
  produced by an ``extract_doc`` call already enforced at count level.
- **REQ-CORPUS-04** — Persisted extraction baseline (``pages.jsonl``)
  under SHA256. Consumed via ``load_pages`` — the chunker refuses to
  operate on drifted input by depending on the baseline the same way
  the embedding stage will.
- **REQ-CHUNK-01** — Chunk size bounded. Every emitted chunk satisfies
  ``token_count(chunk.text) <= MAX_TOKENS``. Enforced by the recursive
  splitter (no atom exceeds ``MAX_TOKENS`` post-split) and the greedy
  merger (never exceeds ``TARGET_TOKENS`` unless a single atom is
  already above it, in which case it emits alone and is still
  ``<= MAX_TOKENS``).
- **REQ-CHUNK-02** — Provenance immutable and falsifiable. Two
  distinct invariants compose:

  * (a) *Literal-substring* — ``page.text[char_start:char_end] ==
    chunk.text`` for every emitted chunk. Trivial by construction:
    :func:`chunk_page` assigns ``Chunk.text = text[char_start:char_end]``
    literally, no rewriting. A future refactor that reconstructs
    ``Chunk.text`` differently (e.g. from atoms) would break this and
    must add its own guard.
  * (b) *Atom contiguity* — the atoms returned by :func:`_split_recursive`
    tile ``text[start:end]`` with no gap. This is the load-bearing
    property: without it, invariant (a) is meaningless (an empty chunk
    would satisfy it). Contiguity rests on
    :func:`_split_keeping_sep_left` (glued-left) and
    :func:`_hard_split_by_tokens` (character-space binary search).

  A VCD consumer at Brique 7 can verify (a) directly on
  ``chunks.jsonl`` + ``pages.jsonl`` without touching a PDF. (b) is
  verified in-repo by ``test_split_recursive_produces_contiguous_atoms``.

Exit codes
----------
``0`` on success (file written or no-op if inputs are empty), ``1`` on
any chunking failure. Errors are aggregated per document rather than
fail-fast.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import tiktoken

REPO_ROOT = Path(__file__).resolve().parent
PAGES_JSONL_PATH = REPO_ROOT / "corpus" / "pages.jsonl"
CHUNKS_JSONL_PATH = REPO_ROOT / "corpus" / "chunks.jsonl"

# Tokenizer identity — frozen at the chunk boundary. Any change here is
# a chunking bump (SHA256 of chunks.jsonl will move, deliberately).
TOKENIZER_NAME = "cl100k_base"

# Target chunk size, greedy merge stops accumulating atoms once total
# tokens would exceed this. Chosen for ANSSI content: a typical
# recommendation (R1, R2, …) plus its explanatory paragraph fits in
# ~300-500 cl100k tokens, so 500 = "one recommendation per chunk" in
# the majority of cases — retrieval alignment with the semantic unit
# the corpus actually indexes.
TARGET_TOKENS = 500

# Hard ceiling — no chunk can exceed this. Enforced by the recursive
# splitter: any atom above the ceiling is refused (falls to hard-split).
# Set to 1.6 × TARGET_TOKENS so a single oversized paragraph doesn't
# trigger hard-split gratuitously — natural boundaries (``\n\n`` then
# ``\n`` then ``. ``) get room to resolve first. Materializes
# REQ-CHUNK-01.
MAX_TOKENS = 800

# Number of tokens duplicated from the tail of chunk N into the head
# of chunk N+1. Prevents a semantic unit (a recommendation) that
# straddles a chunk boundary from appearing complete in neither.
# ~15% of TARGET_TOKENS — RAG-community consensus band (LangChain 20%,
# LlamaIndex 15-20%, Anthropic cookbook 10-15%). Higher = wasteful
# duplication in the embedding store; zero = the "bisected recommendation"
# retrieval failure that costs VCD credibility.
OVERLAP_TOKENS = 75

# Cascade tried in order. The splitter descends only when the current
# level cannot bring every segment below MAX_TOKENS. ``""`` is the
# sentinel triggering hard token-level split — reached only on
# pathological inputs (long URL, base64 blob) that ANSSI PDFs do not
# contain, but present for completeness so no input can defeat the
# contract.
SEPARATORS: tuple[str, ...] = ("\n\n", "\n", ". ", " ", "")

# Fixed JSON field order for chunks.jsonl. json.dumps preserves dict
# insertion order since 3.7, so a stable ordering here keeps the
# committed baseline bit-identical across Python versions. Same idiom
# as extract_all.page_to_json_line.
_CHUNK_KEYS: tuple[str, ...] = (
    "doc_id",
    "page_num",
    "chunk_idx",
    "char_start",
    "char_end",
    "text",
)


class ChunkError(RuntimeError):
    """Base class for any failure raised by the chunking pipeline.

    Distinct from :class:`extract_pdf.ExtractionError` on purpose:
    extraction errors are about the PDF→text transformation, chunk
    errors are about the text→chunks transformation. A downstream
    consumer (embedding stage, VCD) can catch this single type to
    react to "any chunking contract violation".
    """


class ChunkTooLargeError(ChunkError):
    """Hard-split failed to bring a segment under :data:`MAX_TOKENS`.

    Theoretically unreachable given the binary-search hard-split, but
    surfaced explicitly so a hypothetical failure of the underlying
    tokenizer (e.g. a bug where ``encode`` returns fewer tokens than
    ``decode`` needs) doesn't silently emit an oversized chunk.
    """


class InvalidPagesJsonlError(ChunkError):
    """The input ``pages.jsonl`` violated the format contract.

    Distinct from a raw ``json.JSONDecodeError`` so a caller catching
    :class:`ChunkError` at the pipeline boundary still sees the failure.
    """


@dataclass(frozen=True)
class Chunk:
    """One chunk of text extracted from a single page.

    ``doc_id`` and ``page_num`` are copied verbatim from the source
    :class:`~extract_pdf.Page` — the join key toward the manifest and
    the invariant carrier for REQ-CORPUS-02.

    ``chunk_idx`` is 0-indexed **per page**. It restarts at 0 on every
    new page — the ``(doc_id, page_num, chunk_idx)`` triple is the
    stable identifier of a chunk across regenerations, assuming the
    upstream ``pages.jsonl`` is unchanged.

    ``char_start`` / ``char_end`` are byte-free character offsets into
    ``Page.text`` such that ``page.text[char_start:char_end] == text``.
    They are the falsifiability anchor of REQ-CHUNK-02: given a chunk,
    a VCD consumer can reload ``pages.jsonl`` and verify the exact
    citation, without touching a PDF.

    ``text`` is the chunk content — a strict substring of the source
    page.
    """

    doc_id: str
    page_num: int
    chunk_idx: int
    char_start: int
    char_end: int
    text: str


@lru_cache(maxsize=1)
def _tokenizer() -> tiktoken.Encoding:
    """Load the tiktoken encoding once per process.

    ``get_encoding`` is not cheap (it may read the encoded merges
    from disk on first call), and every atom-token count in the
    splitter hits it. Cached at module scope so the CLI, the tests,
    and any programmatic caller share one loaded encoder.
    """
    return tiktoken.get_encoding(TOKENIZER_NAME)


def _token_count(text: str) -> int:
    """Number of ``cl100k_base`` tokens in ``text``.

    Empty string returns 0 — ``tiktoken.encode("")`` = ``[]``.
    """
    return len(_tokenizer().encode(text))


def _split_keeping_sep_left(
    text: str, start: int, end: int, sep: str
) -> list[tuple[int, int]]:
    """Split ``text[start:end]`` on ``sep``, gluing the separator to the
    LEFT piece.

    Returns a list of ``(piece_start, piece_end)`` tuples in absolute
    ``text`` coordinates. The union of the pieces is contiguous and
    covers ``text[start:end]`` exactly — this is what allows any
    concatenation of adjacent atoms downstream to remain a strict
    substring of the source page.

    Requires ``sep != ""`` — the empty-string case is handled upstream
    by hard token-level splitting; ``str.find("", cursor)`` returns
    ``cursor`` unconditionally which would infinite-loop here.

    If ``sep`` does not appear at all in the segment, returns a single
    ``[(start, end)]`` piece (the caller then knows to descend to the
    next separator level).
    """
    assert sep != "", "empty separator must be handled by hard-split"
    pieces: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        pos = text.find(sep, cursor, end)
        if pos == -1:
            pieces.append((cursor, end))
            break
        split_at = pos + len(sep)
        pieces.append((cursor, split_at))
        cursor = split_at
    return pieces or [(start, end)]


def _hard_split_by_tokens(
    text: str, start: int, end: int
) -> list[tuple[int, int]]:
    """Last-resort split: cut ``text[start:end]`` at the largest prefix
    fitting under :data:`MAX_TOKENS`.

    Binary search on character length to find, for each piece, the
    largest prefix whose token count is ``<= MAX_TOKENS``. Character-
    space search (not token-space) so the returned offsets are always
    valid substring bounds — ``tiktoken.decode`` on a truncated token
    list can return a slightly different byte sequence than the source
    when a BPE token straddles the cut, which would break the
    strict-substring invariant.

    Reached only when every semantic separator has failed to reduce a
    segment below :data:`MAX_TOKENS`. On the ANSSI corpus this branch
    is expected to be dead code; present so the contract holds against
    any input.

    Raises :class:`ChunkTooLargeError` if binary search cannot bring a
    piece under :data:`MAX_TOKENS` (would indicate a tokenizer
    inconsistency — surfaced rather than silently emitted).
    """
    pieces: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        remaining = end - cursor
        if _token_count(text[cursor:end]) <= MAX_TOKENS:
            pieces.append((cursor, end))
            break
        lo, hi, best = 1, remaining, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if _token_count(text[cursor : cursor + mid]) <= MAX_TOKENS:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best == 0:
            raise ChunkTooLargeError(
                f"hard-split failed to fit any prefix of "
                f"{remaining} chars under MAX_TOKENS={MAX_TOKENS} — "
                f"tokenizer inconsistency?"
            )
        pieces.append((cursor, cursor + best))
        cursor += best
    return pieces


def _split_recursive(
    text: str, start: int, end: int, sep_idx: int = 0
) -> list[tuple[int, int]]:
    """Recursively split ``text[start:end]`` into atoms all under
    :data:`MAX_TOKENS`.

    At each recursion level, tries the current separator (from
    :data:`SEPARATORS`); if the segment is already small enough, emits
    it as-is; if the separator is absent, descends to the next level;
    if the separator is found, splits and recurses into each piece.

    The final level (empty-string sentinel) triggers hard token-level
    splitting.

    All returned offsets are in absolute ``text`` coordinates.
    """
    if _token_count(text[start:end]) <= MAX_TOKENS:
        return [(start, end)]
    assert sep_idx < len(SEPARATORS), (
        "SEPARATORS lost its empty-string sentinel — the cascade "
        "would never terminate. Restore it in the tuple."
    )
    sep = SEPARATORS[sep_idx]
    if sep == "":
        return _hard_split_by_tokens(text, start, end)
    pieces = _split_keeping_sep_left(text, start, end, sep)
    if len(pieces) == 1:
        return _split_recursive(text, start, end, sep_idx + 1)
    result: list[tuple[int, int]] = []
    for p_start, p_end in pieces:
        result.extend(_split_recursive(text, p_start, p_end, sep_idx + 1))
    return result


def _merge_atoms_to_chunks(
    text: str, atoms: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Greedy merge of atoms into chunks, with overlap.

    Walks atoms left-to-right; accumulates into a chunk while the total
    token count stays ``<= TARGET_TOKENS``. When the next atom would
    overshoot, emits the current chunk and walks BACKWARD from the last
    included atom accumulating tokens until ``OVERLAP_TOKENS`` is
    reached — the next chunk starts at that atom, producing the ~15%
    overlap.

    Overlap is intra-page (the caller only feeds atoms from one page at
    a time), so the overlapping ``[char_start, char_end)`` windows
    always index into the SAME page — never across pages.

    Guard against no-forward-progress: if an oversized single atom (over
    ``TARGET_TOKENS`` but under ``MAX_TOKENS``) is emitted alone, the
    overlap rewind could land back on the same atom, deadlocking the
    walk. When the computed next start ``<= current start``, we force
    ``current + 1``.

    **Documented limit — overlap degrades to zero on adjacent oversized
    atoms.** When two consecutive atoms are each above ``TARGET_TOKENS``
    (i.e. each atom would emit its own single-atom chunk), the backward
    rewind from atom ``j-1 = i`` cannot walk past ``i`` (loop condition
    ``k > i`` is immediately false), so ``overlap == 0`` and
    ``next_i = j``. The two chunks touch but do not share text. Fixing
    this would require intra-atom hard-split for the overlap tail —
    declined as disproportionate:

    * (a) the case is rare: requires consecutive semantic units of
      500+ tokens each, back-to-back, with no ``\\n\\n`` separator to
      break them at the splitter cascade;
    * (b) the bug the overlap policy targets — "one semantic unit
      bisected across two chunks, complete in neither" — cannot
      happen when the unit IS a full atom emitted alone; it is
      complete in its own chunk.

    Frozen as behavior by
    ``test_merge_overlap_degrades_to_zero_on_adjacent_oversized_atoms``.
    """
    if not atoms:
        return []
    atom_tokens = [_token_count(text[s:e]) for s, e in atoms]
    chunks: list[tuple[int, int]] = []

    i = 0
    n = len(atoms)
    while i < n:
        chunk_start = atoms[i][0]
        chunk_end = atoms[i][1]
        tokens_so_far = atom_tokens[i]
        j = i + 1
        while j < n and tokens_so_far + atom_tokens[j] <= TARGET_TOKENS:
            chunk_end = atoms[j][1]
            tokens_so_far += atom_tokens[j]
            j += 1
        chunks.append((chunk_start, chunk_end))
        if j >= n:
            break
        overlap = 0
        k = j - 1
        while k > i and overlap < OVERLAP_TOKENS:
            overlap += atom_tokens[k]
            k -= 1
        next_i = k + 1 if overlap >= OVERLAP_TOKENS else j
        if next_i <= i:
            next_i = i + 1
        i = next_i
    return chunks


def chunk_page(text: str, doc_id: str, page_num: int) -> list[Chunk]:
    """Chunk a single page's text into a list of :class:`Chunk`.

    Empty input (``text == ""``) returns ``[]`` silently. This matches
    :func:`extract_pdf.extract_pages`, which emits ``Page(text="")`` for
    pages that were entirely boilerplate — the chunker treats those as
    "no chunks to produce" rather than failing.

    For every returned chunk, the invariant
    ``text[chunk.char_start:chunk.char_end] == chunk.text`` holds
    (verified by ``test_chunk_page_char_offsets_are_strict_substrings``).
    """
    if not text:
        return []
    atoms = _split_recursive(text, 0, len(text))
    windows = _merge_atoms_to_chunks(text, atoms)
    return [
        Chunk(
            doc_id=doc_id,
            page_num=page_num,
            chunk_idx=idx,
            char_start=start,
            char_end=end,
            text=text[start:end],
        )
        for idx, (start, end) in enumerate(windows)
    ]


def chunk_to_json_line(chunk: Chunk) -> str:
    """Serialize a :class:`Chunk` to a single JSONL line.

    Field order is fixed by :data:`_CHUNK_KEYS` so the committed
    baseline is stable across Python versions. ``ensure_ascii=False``
    keeps French accents readable in ``git diff``. Internal newlines
    inside ``text`` are escaped as ``\\n`` by ``json.dumps``,
    preserving the one-record-per-line JSONL invariant.
    """
    payload = {
        "doc_id": chunk.doc_id,
        "page_num": chunk.page_num,
        "chunk_idx": chunk.chunk_idx,
        "char_start": chunk.char_start,
        "char_end": chunk.char_end,
        "text": chunk.text,
    }
    return json.dumps(payload, ensure_ascii=False)


def _iter_pages_jsonl(path: Path) -> Iterable[tuple[str, int, str]]:
    """Stream ``pages.jsonl`` yielding ``(doc_id, page_num, text)`` tuples.

    Streaming to bound memory: even at 1000+ pages the file fits in
    memory today (~2 MB), but staying stream-based keeps the algorithm
    intact if the corpus grows an order of magnitude.

    Raises :class:`InvalidPagesJsonlError` on a JSON decode failure or
    a missing required key.
    """
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            raw = raw.rstrip("\n")
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError as err:
                raise InvalidPagesJsonlError(
                    f"{path}:{line_no} — invalid JSON: {err}"
                ) from err
            try:
                yield (record["doc_id"], record["page_num"], record["text"])
            except KeyError as err:
                raise InvalidPagesJsonlError(
                    f"{path}:{line_no} — missing key {err}"
                ) from err


@dataclass
class ChunkReport:
    """Aggregated result of a corpus-wide chunking pass.

    ``.ok`` is the single boolean the caller checks to decide whether
    the write happened. ``errors`` carries human-readable messages.
    """

    pages_processed: int = 0
    chunks_written: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []

    @property
    def ok(self) -> bool:
        return not self.errors


def chunk_all(pages_jsonl: Path, output_path: Path) -> ChunkReport:
    """Chunk every page from ``pages_jsonl`` and stream results to
    ``output_path``.

    Streams both sides: reads ``pages.jsonl`` line by line, writes
    ``chunks.jsonl`` via a ``.tmp`` sidecar renamed atomically on
    success. A crash mid-run leaves the previous ``chunks.jsonl``
    intact; the sidecar is deleted on any failure.

    Errors during chunking (a page whose text contains no separator
    at all and hits the hard-split ceiling, tokenizer failures, …)
    are aggregated into the report and the next page is attempted —
    a partial report is less useful than a full inventory.
    """
    report = ChunkReport()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".tmp")

    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as out:
            for doc_id, page_num, text in _iter_pages_jsonl(pages_jsonl):
                report.pages_processed += 1
                try:
                    chunks = chunk_page(text, doc_id, page_num)
                except ChunkError as err:
                    report.errors.append(
                        f"[{doc_id} p.{page_num}] {err.__class__.__name__}: {err}"
                    )
                    continue
                for chunk in chunks:
                    out.write(chunk_to_json_line(chunk))
                    out.write("\n")
                    report.chunks_written += 1
        if report.ok:
            tmp_path.replace(output_path)
        else:
            tmp_path.unlink(missing_ok=True)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return report


def main() -> int:
    if not PAGES_JSONL_PATH.exists():
        print(
            f"ERROR: {PAGES_JSONL_PATH} missing. Run extract_all.py first.",
            file=sys.stderr,
        )
        return 1

    print(f"Chunking {PAGES_JSONL_PATH} -> {CHUNKS_JSONL_PATH}")
    print(
        f"  tokenizer={TOKENIZER_NAME} "
        f"target={TARGET_TOKENS} max={MAX_TOKENS} "
        f"overlap={OVERLAP_TOKENS}"
    )

    report = chunk_all(PAGES_JSONL_PATH, CHUNKS_JSONL_PATH)

    if report.errors:
        print("\n=== CHUNKING FAILURES ===", file=sys.stderr)
        for err in report.errors:
            print(err, file=sys.stderr)
        return 1

    print(
        f"\nProcessed {report.pages_processed} page(s), "
        f"emitted {report.chunks_written} chunk(s) -> {CHUNKS_JSONL_PATH}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
