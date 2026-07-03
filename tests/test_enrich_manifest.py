"""Tests for enrich_manifest.py — validates the manifest enrichment contract.

Le champ `pages` du manifest matérialise l'invariant `page_ref <= pages(doc)`
qui sera exploité par la vérif de provenance des chunks (Brique 2/3).
`bytes` est le sanity check amont pour les docs signed_url dont le
téléchargement est manuel. Les deux deviennent donc contrat au même
titre que sha256 une fois écrits — d'où la batterie de tests ci-dessous
qui matérialise les invariants du script.

Tests critiques :
- (a) enrichissement d'un manifest vierge → valeurs exactes bien lues par yaml
- (b) idempotence bit-à-bit — un 2ᵉ run ne modifie pas le fichier
- (c) commentaires d'en-tête préservés (contrat documenté du manifest)
- (d) sha256 dupliqués → ValueError avant toute écriture (précondition)
- (e) PDF manquant → exit non-zéro SANS écriture partielle (atomicité)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from reportlab.pdfgen import canvas

import enrich_manifest


HEADER_COMMENTS = (
    "# Test manifest — fixture pour enrich_manifest\n"
    "# Ces commentaires doivent survivre à l'enrichissement.\n"
    "# (Contrat de préservation d'en-tête, cf. test_header_comments_preserved.)\n"
    "\n"
)


def _make_pdf(path: Path, pages: int) -> None:
    """Écrit un PDF valide de ``pages`` pages via reportlab (test-only)."""
    c = canvas.Canvas(str(path))
    for _ in range(pages):
        c.drawString(100, 100, "test")
        c.showPage()
    c.save()


def _make_manifest(path: Path, docs: list[dict]) -> None:
    body = yaml.safe_dump(
        {
            "schema_version": 1,
            "license": "Test",
            "source_authority": "TEST",
            "documents": docs,
        },
        sort_keys=False,
    )
    # newline="" pour ne pas laisser Python translater \n en \r\n sur
    # Windows — les tests d'idempotence exigent un contrôle strict.
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(HEADER_COMMENTS + body)


@pytest.fixture
def two_docs(tmp_path: Path) -> tuple[Path, Path]:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()

    pdf_a = pdf_dir / "a.pdf"
    _make_pdf(pdf_a, pages=3)
    pdf_b = pdf_dir / "b.pdf"
    _make_pdf(pdf_b, pages=7)

    docs = [
        {
            "doc_id": "doc-a",
            "filename": "a.pdf",
            "sha256": hashlib.sha256(pdf_a.read_bytes()).hexdigest(),
        },
        {
            "doc_id": "doc-b",
            "filename": "b.pdf",
            "sha256": hashlib.sha256(pdf_b.read_bytes()).hexdigest(),
        },
    ]
    manifest_path = tmp_path / "manifest.yaml"
    _make_manifest(manifest_path, docs)
    return manifest_path, pdf_dir


def test_enrichment_injects_bytes_and_pages_per_doc(two_docs) -> None:
    manifest_path, pdf_dir = two_docs
    exit_code = enrich_manifest.enrich(manifest_path, pdf_dir)
    assert exit_code == 0

    enriched = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    docs = {d["doc_id"]: d for d in enriched["documents"]}

    expected_bytes_a = (pdf_dir / "a.pdf").stat().st_size
    expected_bytes_b = (pdf_dir / "b.pdf").stat().st_size

    assert docs["doc-a"]["bytes"] == expected_bytes_a
    assert docs["doc-a"]["pages"] == 3
    assert docs["doc-b"]["bytes"] == expected_bytes_b
    assert docs["doc-b"]["pages"] == 7


def test_enrichment_is_bit_for_bit_idempotent(two_docs) -> None:
    manifest_path, pdf_dir = two_docs
    enrich_manifest.enrich(manifest_path, pdf_dir)
    hash_after_first = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    exit_code = enrich_manifest.enrich(manifest_path, pdf_dir)
    hash_after_second = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    assert exit_code == 0
    assert hash_after_first == hash_after_second, (
        "manifest modifié lors d'un 2ᵉ run — idempotence bit-à-bit rompue"
    )


def test_header_comments_preserved(two_docs) -> None:
    manifest_path, pdf_dir = two_docs
    enrich_manifest.enrich(manifest_path, pdf_dir)

    text = manifest_path.read_text(encoding="utf-8")
    for line in HEADER_COMMENTS.strip().splitlines():
        assert line in text, f"commentaire d'en-tête perdu : {line!r}"


def test_duplicate_sha256_raises_valueerror_without_writing(tmp_path: Path) -> None:
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_a = pdf_dir / "a.pdf"
    _make_pdf(pdf_a, pages=1)
    same_hash = hashlib.sha256(pdf_a.read_bytes()).hexdigest()

    # Collision manifeste : deux docs déclarent le MÊME sha256.
    # Reproduit un copier-coller d'entrée de manifest — cas pathologique
    # que le contrat d'identité binaire doit refuser explicitement.
    docs = [
        {"doc_id": "a", "filename": "a.pdf", "sha256": same_hash},
        {"doc_id": "b", "filename": "b.pdf", "sha256": same_hash},
    ]
    manifest_path = tmp_path / "manifest.yaml"
    _make_manifest(manifest_path, docs)
    before = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    with pytest.raises(ValueError) as excinfo:
        enrich_manifest.enrich(manifest_path, pdf_dir)
    assert "sha256" in str(excinfo.value).lower()

    after = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert before == after, "manifest modifié malgré rejet de la précondition"


def test_missing_pdf_exits_nonzero_without_writing(two_docs) -> None:
    manifest_path, pdf_dir = two_docs
    (pdf_dir / "a.pdf").unlink()

    before = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    exit_code = enrich_manifest.enrich(manifest_path, pdf_dir)
    after = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    assert exit_code != 0
    assert before == after, "manifest modifié malgré échec — écriture non-atomique"


def test_partial_enrichment_raises_valueerror_without_writing(tmp_path: Path) -> None:
    """Un doc avec `bytes` mais pas `pages` (ou l'inverse) est un état
    semi-enrichi incohérent — cf. bug relevé en review : l'insertion par
    match sha256 ne sait pas cibler un champ précis et dupliquerait
    `bytes:` si on laissait passer ce cas."""
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_a = pdf_dir / "a.pdf"
    _make_pdf(pdf_a, pages=1)

    docs = [
        {
            "doc_id": "a",
            "filename": "a.pdf",
            "sha256": hashlib.sha256(pdf_a.read_bytes()).hexdigest(),
            "bytes": 123,  # présent seul, sans `pages` — état incohérent
        },
    ]
    manifest_path = tmp_path / "manifest.yaml"
    _make_manifest(manifest_path, docs)
    before = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    with pytest.raises(ValueError) as excinfo:
        enrich_manifest.enrich(manifest_path, pdf_dir)
    assert "semi-enrichi" in str(excinfo.value).lower()

    after = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert before == after, "manifest modifié malgré rejet de la précondition"


def test_matcher_rejects_lookalike_lines() -> None:
    """Sentinelle de non-régression du contrat de forme de ``SHA_LINE_RE``.

    Le 1ᵉʳ verdict senior avait pointé ``startswith("sha256:")`` comme
    fragile face à des variantes visuellement proches. Ce test fige le
    comportement du regex : matche uniquement la forme canonique
    ``<indent>sha256: <hex64>``, refuse tout lookalike.
    """
    hex64 = "a" * 64
    valid = f"    sha256: {hex64}\n"
    assert enrich_manifest.SHA_LINE_RE.match(valid) is not None

    lookalikes = [
        f"sha256: {hex64}\n",              # pas d'indent
        f"    sha256_source: {hex64}\n",   # préfixe étendu
        f"    # sha256: {hex64}\n",        # ligne de commentaire
        f"    sha256: {'a' * 63}\n",       # hash trop court
        f"    sha256: {hex64} extra\n",    # trailing content
    ]
    for line in lookalikes:
        assert enrich_manifest.SHA_LINE_RE.match(line) is None, (
            f"lookalike accepté à tort : {line!r}"
        )
