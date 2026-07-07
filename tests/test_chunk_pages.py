"""Tests for chunk_pages.py.

Two layers:

1. **Unit tests** — drive :func:`chunk_pages.chunk_page` and the
   internal splitters on deterministic in-memory strings. Cover the
   contract of the chunker (bounded size, strict-substring provenance,
   overlap, determinism, cascade descent, empty input, error typing).
2. **Baseline tests on ``corpus/pages.jsonl``** — always run (the file
   is versioned, so present on a fresh clone). Verify the committed
   input is chunkable end-to-end without any chunk violating
   REQ-CHUNK-01 (size bound) or REQ-CHUNK-02 (strict-substring
   provenance), and that no chunk crosses a page boundary — the
   chunk-side leg of REQ-CORPUS-02 becoming *fully enforced*.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from chunk_pages import (
    MAX_TOKENS,
    OVERLAP_TOKENS,
    SEPARATORS,
    TARGET_TOKENS,
    Chunk,
    ChunkError,
    ChunkReport,
    ChunkTooLargeError,
    InvalidPagesJsonlError,
    _hard_split_by_tokens,
    _merge_atoms_to_chunks,
    _split_keeping_sep_left,
    _split_recursive,
    _token_count,
    chunk_all,
    chunk_page,
    chunk_to_json_line,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGES_JSONL_PATH = REPO_ROOT / "corpus" / "pages.jsonl"
CHUNKS_JSONL_PATH = REPO_ROOT / "corpus" / "chunks.jsonl"
MANIFEST_PATH = REPO_ROOT / "corpus" / "manifest.yaml"


# ---------------------------------------------------------------------
# Tokenizer sanity — thin but pins the identity we advertise
# ---------------------------------------------------------------------


def test_token_count_empty_string_is_zero():
    """Empty page (post-strip boilerplate) must produce zero chunks
    downstream — this identity anchors ``chunk_page("") -> []``.
    """
    assert _token_count("") == 0


def test_token_count_grows_with_content():
    assert _token_count("bonjour") >= 1
    assert _token_count("bonjour tout le monde") > _token_count("bonjour")


# ---------------------------------------------------------------------
# _split_keeping_sep_left : contiguous coverage
# ---------------------------------------------------------------------


def test_split_keeping_sep_left_covers_input_contiguously():
    """Left-glued split must leave no gap between pieces.

    This is the property that makes any concatenation of adjacent atoms
    a strict substring of the source — foundation of REQ-CHUNK-02.
    """
    text = "para1\n\npara2\n\npara3"
    pieces = _split_keeping_sep_left(text, 0, len(text), "\n\n")
    joined = "".join(text[s:e] for s, e in pieces)
    assert joined == text


def test_split_keeping_sep_left_glues_separator_to_left_piece():
    text = "a\n\nb"
    pieces = _split_keeping_sep_left(text, 0, len(text), "\n\n")
    # Two pieces: "a\n\n" and "b"
    assert len(pieces) == 2
    assert text[pieces[0][0]:pieces[0][1]] == "a\n\n"
    assert text[pieces[1][0]:pieces[1][1]] == "b"


def test_split_keeping_sep_left_absent_separator_returns_single_piece():
    text = "no separator here at all"
    pieces = _split_keeping_sep_left(text, 0, len(text), "\n\n")
    assert pieces == [(0, len(text))]


def test_split_keeping_sep_left_rejects_empty_separator():
    """Empty separator would infinite-loop str.find; hard-split branch
    handles it upstream. Assert kept as a defensive contract signal.
    """
    with pytest.raises(AssertionError):
        _split_keeping_sep_left("abc", 0, 3, "")


# ---------------------------------------------------------------------
# _hard_split_by_tokens : last-resort ceiling enforcement
# ---------------------------------------------------------------------


def test_hard_split_produces_pieces_all_under_max_tokens():
    """Long token-dense string with no whitespace at all (pathological
    input: a base64 blob, a URL). Hard-split must still bring every
    piece under MAX_TOKENS.
    """
    text = "a" * 20000  # will vastly exceed MAX_TOKENS
    pieces = _hard_split_by_tokens(text, 0, len(text))
    for start, end in pieces:
        assert _token_count(text[start:end]) <= MAX_TOKENS


def test_hard_split_pieces_are_contiguous_and_cover_input():
    text = "abcdefg" * 500
    pieces = _hard_split_by_tokens(text, 0, len(text))
    joined = "".join(text[s:e] for s, e in pieces)
    assert joined == text


def test_hard_split_short_input_returns_single_piece():
    text = "short"
    pieces = _hard_split_by_tokens(text, 0, len(text))
    assert pieces == [(0, len(text))]


# ---------------------------------------------------------------------
# _split_recursive : cascade descent
# ---------------------------------------------------------------------


def test_split_recursive_short_text_not_split():
    text = "a short paragraph"
    atoms = _split_recursive(text, 0, len(text))
    assert atoms == [(0, len(text))]


def test_split_recursive_descends_through_cascade():
    """Text with only mid-priority separators (``\\n``) triggers cascade
    from ``\\n\\n`` (absent) down to ``\\n`` (present). Must produce
    multiple atoms even though the top-priority separator is absent.
    """
    # Force real over-MAX size — build a long single-blob no-paragraph
    long_line = "phrase sans point ni deux sauts de ligne " * 200
    text = long_line + "\n" + long_line + "\n" + long_line
    assert _token_count(text) > MAX_TOKENS  # precondition
    atoms = _split_recursive(text, 0, len(text))
    assert len(atoms) >= 2
    for start, end in atoms:
        assert _token_count(text[start:end]) <= MAX_TOKENS


def test_split_recursive_produces_contiguous_atoms():
    """After cascade + hard-split, atoms cover the input with no gap.

    This is the load-bearing property for the strict-substring
    provenance downstream — verified once here on a mixed-separator
    input.
    """
    text = (
        ("Paragraphe long. " * 50) + "\n\n"
        + ("Autre paragraphe. " * 50) + "\n\n"
        + ("Fin. " * 50)
    )
    atoms = _split_recursive(text, 0, len(text))
    joined = "".join(text[s:e] for s, e in atoms)
    assert joined == text


# ---------------------------------------------------------------------
# _merge_atoms_to_chunks : greedy + overlap
# ---------------------------------------------------------------------


def test_merge_atoms_makes_forward_progress_on_oversized_single_atom():
    """A single atom above TARGET_TOKENS but under MAX_TOKENS emits
    alone. The overlap rewind must not land back on the same atom
    (would deadlock the walk).

    Guarded by the ``next_i <= i`` fallback in ``_merge_atoms_to_chunks``.
    """
    # Build a text where one segment is >TARGET but <MAX with no split
    # opportunity. "aaa..." repeated hits the token target with no
    # separator in the whole string.
    big_atom_text = "aaa " * 500
    atoms = _split_recursive(big_atom_text, 0, len(big_atom_text))
    windows = _merge_atoms_to_chunks(big_atom_text, atoms)
    # No infinite loop, and full coverage of the input
    assert windows[0][0] == 0
    assert windows[-1][1] == len(big_atom_text)


# ---------------------------------------------------------------------
# chunk_page : public contract
# ---------------------------------------------------------------------


def test_chunk_page_empty_text_returns_empty_list():
    """Post-strip boilerplate pages have ``text == ""`` (see
    :class:`extract_pdf.Page`). Chunker must silently emit zero chunks
    on those — mirrors the behavior of the upstream extractor.
    """
    assert chunk_page("", "doc", 1) == []


def test_chunk_page_short_text_returns_single_chunk_covering_full_input():
    text = "Une phrase courte."
    chunks = chunk_page(text, "doc", 1)
    assert len(chunks) == 1
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len(text)
    assert chunks[0].text == text


def test_chunk_page_char_offsets_are_strict_substrings_of_source():
    """THE falsifiability test for REQ-CHUNK-02.

    For every emitted chunk, ``page.text[chunk.char_start:chunk.char_end]``
    must equal ``chunk.text`` exactly. A regression here (rounding,
    off-by-one, whitespace normalization slipped in) makes the citation
    contract of the VCD Brique 7 uncitable.

    Uses a text large enough to force multi-chunk output.
    """
    text = ("Un paragraphe explicatif de la recommandation. " * 80) + "\n\n" + \
           ("Une seconde recommandation avec beaucoup de contenu. " * 80)
    chunks = chunk_page(text, "doc", 42)
    assert len(chunks) >= 2  # precondition: multi-chunk
    for c in chunks:
        assert text[c.char_start:c.char_end] == c.text, (
            f"chunk_idx={c.chunk_idx} strict-substring invariant broken"
        )


def test_chunk_page_every_chunk_stays_under_max_tokens():
    """Falsifiability test for REQ-CHUNK-01.

    On multi-chunk output, every emitted chunk must satisfy
    ``token_count(chunk.text) <= MAX_TOKENS``.
    """
    text = ("Un contenu conséquent. " * 200) + "\n\n" + \
           ("Une autre section. " * 200)
    chunks = chunk_page(text, "doc", 7)
    for c in chunks:
        assert _token_count(c.text) <= MAX_TOKENS


def test_chunk_page_chunk_idx_is_zero_indexed_and_contiguous():
    text = ("Contenu. " * 500)
    chunks = chunk_page(text, "doc", 1)
    assert [c.chunk_idx for c in chunks] == list(range(len(chunks)))


def test_chunk_page_propagates_doc_id_and_page_num_verbatim():
    """Provenance carrier for REQ-CORPUS-02 fully-enforced status.

    ``(doc_id, page_num)`` on every emitted chunk must equal the
    values passed to ``chunk_page`` — no rewrite, no mutation.
    """
    text = "Court."
    chunks = chunk_page(text, "some-doc-id", 123)
    assert all(c.doc_id == "some-doc-id" for c in chunks)
    assert all(c.page_num == 123 for c in chunks)


def test_chunk_page_consecutive_chunks_overlap_intra_page():
    """Consecutive chunks share text (overlap policy). The overlap is
    materialized as ``chunk[i+1].char_start < chunk[i].char_end``.

    Both windows index into the SAME page — the overlap is intra-page
    by construction, never cross-page.
    """
    text = ("Contenu long. " * 300) + "\n\n" + ("Autre. " * 300)
    chunks = chunk_page(text, "doc", 1)
    assert len(chunks) >= 2
    for i in range(1, len(chunks)):
        assert chunks[i].char_start < chunks[i - 1].char_end, (
            f"no overlap between chunk {i-1} and {i}"
        )


def test_chunk_page_overlap_approaches_target_size_when_reachable():
    """Overlap should approximate OVERLAP_TOKENS on most transitions.

    Loose bound (``>= OVERLAP_TOKENS / 3``) because greedy merge on
    fine-grained atoms may undershoot: the walk stops as soon as the
    accumulator reaches OVERLAP_TOKENS, which on tiny atoms can land
    slightly above the target — but never a full order of magnitude
    below.
    """
    text = ("Un contenu récurrent. " * 300)
    chunks = chunk_page(text, "doc", 1)
    assert len(chunks) >= 2
    overlaps = []
    for i in range(1, len(chunks)):
        overlap_text = text[chunks[i].char_start:chunks[i - 1].char_end]
        overlaps.append(_token_count(overlap_text))
    # At least one transition should approach the target overlap
    assert max(overlaps) >= OVERLAP_TOKENS / 3


def test_chunk_page_is_deterministic():
    """Same input → same chunks bit-for-bit.

    Foundation of the SHA256 lock on ``chunks.jsonl`` (Brique 1 lot
    final). If chunk_page were non-deterministic, the baseline would
    drift on every regeneration.
    """
    text = ("Une reco importante. " * 200) + "\n\n" + ("Suite. " * 200)
    chunks1 = chunk_page(text, "doc", 5)
    chunks2 = chunk_page(text, "doc", 5)
    assert chunks1 == chunks2


# ---------------------------------------------------------------------
# chunk_to_json_line : serialization contract
# ---------------------------------------------------------------------


def test_chunk_to_json_line_has_fixed_key_order():
    """Baseline stability across Python versions and re-runs."""
    chunk = Chunk(
        doc_id="d", page_num=1, chunk_idx=0,
        char_start=0, char_end=5, text="hello",
    )
    line = chunk_to_json_line(chunk)
    order = ["doc_id", "page_num", "chunk_idx", "char_start", "char_end", "text"]
    positions = [line.index(f'"{k}"') for k in order]
    assert positions == sorted(positions)


def test_chunk_to_json_line_preserves_utf8_natively():
    """``ensure_ascii=False`` — French accents readable in ``git diff``.

    Regression sentinel: flipping to ``ensure_ascii=True`` roughly
    doubles the baseline size and makes diffs unreadable.
    """
    chunk = Chunk(
        doc_id="d", page_num=1, chunk_idx=0,
        char_start=0, char_end=9, text="préréquis",
    )
    line = chunk_to_json_line(chunk)
    assert "préréquis" in line
    assert "\\u" not in line


def test_chunk_to_json_line_escapes_internal_newlines():
    """Chunks always contain newlines (paragraph joins). They MUST be
    escaped so the one-record-per-line JSONL invariant holds.
    """
    chunk = Chunk(
        doc_id="d", page_num=1, chunk_idx=0,
        char_start=0, char_end=11, text="line1\nline2",
    )
    line = chunk_to_json_line(chunk)
    assert "\n" not in line
    assert "\\n" in line
    assert json.loads(line)["text"] == "line1\nline2"


# ---------------------------------------------------------------------
# chunk_all : end-to-end write contract, atomic sidecar
# ---------------------------------------------------------------------


def test_chunk_all_writes_jsonl_atomically(tmp_path):
    """One record per line, valid JSON, keys match the fixed order.

    Fixture uses tiny synthetic pages so the test is fast and does
    not depend on the corpus file being present.
    """
    input_path = tmp_path / "pages.jsonl"
    output_path = tmp_path / "chunks.jsonl"
    input_path.write_text(
        '{"doc_id":"a","page_num":1,"text":"Un contenu court."}\n'
        '{"doc_id":"a","page_num":2,"text":""}\n'
        '{"doc_id":"b","page_num":1,"text":"Une autre page."}\n',
        encoding="utf-8",
    )
    report = chunk_all(input_path, output_path)
    assert report.ok
    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == report.chunks_written
    for line in lines:
        record = json.loads(line)
        assert set(record) == {
            "doc_id", "page_num", "chunk_idx",
            "char_start", "char_end", "text",
        }


def test_chunk_all_skips_empty_pages_silently(tmp_path):
    """Empty pages (post-strip boilerplate) contribute zero chunks
    without raising — mirrors :func:`chunk_page` behavior.
    """
    input_path = tmp_path / "pages.jsonl"
    output_path = tmp_path / "chunks.jsonl"
    input_path.write_text(
        '{"doc_id":"a","page_num":1,"text":""}\n'
        '{"doc_id":"a","page_num":2,"text":""}\n',
        encoding="utf-8",
    )
    report = chunk_all(input_path, output_path)
    assert report.ok
    assert report.pages_processed == 2
    assert report.chunks_written == 0


def test_chunk_all_second_run_is_bit_for_bit_identical(tmp_path):
    """Determinism at the CLI level.

    Foundation for the SHA256 lock on ``chunks.jsonl``. Fixture is
    self-contained so this runs on any machine without touching the
    corpus.
    """
    input_path = tmp_path / "pages.jsonl"
    input_path.write_text(
        '{"doc_id":"x","page_num":1,"text":"Un texte de test. Court."}\n'
        '{"doc_id":"x","page_num":2,"text":"' + ("Long. " * 300) + '"}\n',
        encoding="utf-8",
    )
    out1 = tmp_path / "chunks-1.jsonl"
    out2 = tmp_path / "chunks-2.jsonl"
    chunk_all(input_path, out1)
    chunk_all(input_path, out2)
    assert out1.read_bytes() == out2.read_bytes()


def test_chunk_all_atomic_sidecar_not_left_on_success(tmp_path):
    """Temp sidecar renamed atomically on success — no ``.tmp`` residue.

    A residue would confuse a next-run diff and, worse, mask a
    partially-written baseline on a crashed run.
    """
    input_path = tmp_path / "pages.jsonl"
    output_path = tmp_path / "chunks.jsonl"
    input_path.write_text(
        '{"doc_id":"a","page_num":1,"text":"Court."}\n',
        encoding="utf-8",
    )
    chunk_all(input_path, output_path)
    assert output_path.exists()
    assert not output_path.with_suffix(output_path.suffix + ".tmp").exists()


def test_chunk_all_raises_on_invalid_json_input(tmp_path):
    """A malformed input line must surface as
    :class:`InvalidPagesJsonlError` — catchable by consumers via
    :class:`ChunkError`.
    """
    input_path = tmp_path / "pages.jsonl"
    output_path = tmp_path / "chunks.jsonl"
    input_path.write_text("{not-json\n", encoding="utf-8")
    with pytest.raises(InvalidPagesJsonlError):
        chunk_all(input_path, output_path)
    # Catchable as ChunkError umbrella
    assert issubclass(InvalidPagesJsonlError, ChunkError)


def test_chunk_all_raises_on_missing_required_key(tmp_path):
    input_path = tmp_path / "pages.jsonl"
    output_path = tmp_path / "chunks.jsonl"
    input_path.write_text(
        '{"doc_id":"a","page_num":1}\n',  # missing "text"
        encoding="utf-8",
    )
    with pytest.raises(InvalidPagesJsonlError):
        chunk_all(input_path, output_path)


# ---------------------------------------------------------------------
# Baseline tests on the versioned corpus/pages.jsonl
# ---------------------------------------------------------------------


def _load_baseline_pages() -> list[tuple[str, int, str]]:
    if not PAGES_JSONL_PATH.exists():
        pytest.skip(f"{PAGES_JSONL_PATH} missing — run extract_all.py")
    pages = []
    with PAGES_JSONL_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            rec = json.loads(line)
            pages.append((rec["doc_id"], rec["page_num"], rec["text"]))
    return pages


def test_baseline_every_chunk_under_max_tokens_on_real_corpus():
    """REQ-CHUNK-01 verified end-to-end on the versioned baseline.

    Any regression bringing a chunk above MAX_TOKENS (broken cascade,
    tokenizer change without constant bump) surfaces here without
    running the CLI.
    """
    pages = _load_baseline_pages()
    for doc_id, page_num, text in pages:
        for chunk in chunk_page(text, doc_id, page_num):
            n = _token_count(chunk.text)
            assert n <= MAX_TOKENS, (
                f"{doc_id} p.{page_num} chunk_idx={chunk.chunk_idx} "
                f"exceeds MAX_TOKENS: {n} > {MAX_TOKENS}"
            )


def test_baseline_strict_substring_invariant_on_real_corpus():
    """REQ-CHUNK-02 verified end-to-end.

    For every (doc, page, chunk) on the real corpus:
    ``page.text[char_start:char_end] == chunk.text``.
    """
    pages = _load_baseline_pages()
    for doc_id, page_num, text in pages:
        for chunk in chunk_page(text, doc_id, page_num):
            assert text[chunk.char_start:chunk.char_end] == chunk.text, (
                f"{doc_id} p.{page_num} chunk_idx={chunk.chunk_idx} "
                f"strict-substring broken"
            )


def test_baseline_no_chunk_crosses_page_boundary_on_real_corpus():
    """REQ-CORPUS-02 chunk-side leg — *fully enforced*.

    ``(doc_id, page_num)`` on the chunk equals ``(doc_id, page_num)``
    of exactly one source Page. Trivially true by construction of
    ``chunk_page`` (called per-page), asserted here as a
    machine-readable statement of the invariant.
    """
    pages = _load_baseline_pages()
    manifest_key = {(doc_id, page_num) for doc_id, page_num, _ in pages}
    for doc_id, page_num, text in pages:
        for chunk in chunk_page(text, doc_id, page_num):
            assert (chunk.doc_id, chunk.page_num) == (doc_id, page_num)
            assert (chunk.doc_id, chunk.page_num) in manifest_key


# ---------------------------------------------------------------------
# Baseline SHA256 lock — miroir de test_baseline_hash_matches_manifest
# côté pages_jsonl. Machine-enforced end-to-end.
# ---------------------------------------------------------------------


def test_chunks_baseline_hash_matches_manifest():
    """Bit-for-bit contract on the committed ``corpus/chunks.jsonl``.

    Mirrors REQ-CORPUS-04 for the chunk artifact. Any drift — a
    tokenizer change, a constant bump, a whitespace-only regression
    in the splitter — fails here without any human ``git diff``.

    Repair procedure on failure: rerun ``python chunk_pages.py`` and
    bump ``derived_artifacts.chunks_jsonl.sha256`` and ``.bytes`` in
    ``corpus/manifest.yaml`` deliberately.
    """
    if not CHUNKS_JSONL_PATH.exists():
        pytest.skip(
            f"{CHUNKS_JSONL_PATH} missing — run python chunk_pages.py"
        )
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    declared = manifest["derived_artifacts"]["chunks_jsonl"]["sha256"]
    actual = hashlib.sha256(CHUNKS_JSONL_PATH.read_bytes()).hexdigest()
    assert actual == declared, (
        f"corpus/chunks.jsonl SHA256 mismatch — baseline drift.\n"
        f"  declared: {declared}\n"
        f"  actual:   {actual}\n"
        f"  Rerun `python chunk_pages.py` and bump "
        f"derived_artifacts.chunks_jsonl.sha256 in corpus/manifest.yaml."
    )


def test_chunks_baseline_uses_lf_line_endings_only():
    """Platform-invariant bit-for-bit contract.

    A CR byte anywhere means either CRLF checkout translation active
    (``.gitattributes`` broken) or a writer no longer forcing
    ``newline="\\n"``. Either breaks the SHA256 contract silently.
    """
    if not CHUNKS_JSONL_PATH.exists():
        pytest.skip(f"{CHUNKS_JSONL_PATH} missing")
    raw = CHUNKS_JSONL_PATH.read_bytes()
    assert b"\r" not in raw, (
        "CR byte(s) found in chunks.jsonl — verify .gitattributes "
        "and Git checkout on this platform."
    )


def test_chunks_baseline_bytes_matches_manifest():
    """Independent size check next to the SHA256 lock.

    Cheap secondary signal — a size mismatch surfaces "wrong file" vs
    "content drift" faster than SHA256 alone.
    """
    if not CHUNKS_JSONL_PATH.exists():
        pytest.skip(f"{CHUNKS_JSONL_PATH} missing")
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    declared = manifest["derived_artifacts"]["chunks_jsonl"]["bytes"]
    actual = CHUNKS_JSONL_PATH.stat().st_size
    assert actual == declared
