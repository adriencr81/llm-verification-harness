"""Tests for extract_pdf.py.

Fixture PDFs are generated deterministically via reportlab so tests
never depend on the real (git-ignored) corpus PDFs. One integration
test hits ``corpus/pdfs/reco-active-directory.pdf`` for a real-world
sanity check on the header/footer stripper — skipped when the file is
absent (fresh clone before ``download_corpus.py`` has run).

Invariants under test:
- ordre + contiguïté des pages : ``page_num == list(range(1, N+1))``.
- doc_id propagé tel quel sur chaque Page.
- REQ-CORPUS-02 enforced : ``expected_page_count`` mismatch raises.
- header/footer répété stripped, contenu unique préservé.
- normalisation digits→# empêche les numéros de page de defeater le
  détecteur de bruit.
- petits docs (< MIN_PAGES_FOR_STRIP) pas strippés.
- erreurs typées levées correctement (PDF absent, doc_id inconnu,
  extraction vide).
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest
import yaml
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from download_corpus import CorpusError
from extract_pdf import (
    EmptyExtractionError,
    ExtractionError,
    MIN_PAGES_FOR_STRIP,
    NOISE_THRESHOLD,
    Page,
    PageCountMismatchError,
    PdfMissingError,
    UnknownDocIdError,
    _is_page_number_only,
    _normalize_line,
    _strip_noise,
    extract_doc,
    extract_pages,
    load_manifest,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_PDFS_DIR = REPO_ROOT / "corpus" / "pdfs"


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


# --- extract_pages : structure de sortie ---


def test_extract_pages_returns_1_indexed_contiguous_pages(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(
        pdf,
        [
            ["contenu A"],
            ["contenu B"],
            ["contenu C"],
        ],
    )
    pages = extract_pages(pdf, doc_id="fixture")
    assert [p.page_num for p in pages] == [1, 2, 3]
    assert all(isinstance(p, Page) for p in pages)


def test_extract_pages_propagates_doc_id(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, [["a"], ["b"]])
    pages = extract_pages(pdf, doc_id="my-guide-42")
    assert all(p.doc_id == "my-guide-42" for p in pages)


# --- header/footer stripping ---


def test_extract_pages_strips_repeated_header_and_footer(tmp_path):
    pdf = tmp_path / "doc.pdf"
    header = "ANSSI Guide hygiene"
    footer = "Version 2.0 Etalab"
    _make_pdf(
        pdf,
        [
            [header, "un contenu unique A", footer],
            [header, "un contenu unique B", footer],
            [header, "un contenu unique C", footer],
            [header, "un contenu unique D", footer],
        ],
    )
    pages = extract_pages(pdf, doc_id="fx")
    for p in pages:
        assert header not in p.text
        assert footer not in p.text
    assert "un contenu unique A" in pages[0].text
    assert "un contenu unique D" in pages[3].text


def test_extract_pages_preserves_unique_content(tmp_path):
    """Aucune ligne ne se répète — rien ne doit être strippé."""
    pdf = tmp_path / "doc.pdf"
    _make_pdf(
        pdf,
        [
            ["ligne A1", "ligne A2"],
            ["ligne B1", "ligne B2"],
            ["ligne C1", "ligne C2"],
        ],
    )
    pages = extract_pages(pdf, doc_id="x")
    all_text = "\n".join(p.text for p in pages)
    for marker in ["ligne A1", "ligne A2", "ligne B1", "ligne B2", "ligne C1"]:
        assert marker in all_text


def test_extract_pages_page_numbers_do_not_defeat_stripping(tmp_path):
    """Numéros de page (14, 15, 16…) normalisés en '#' → détectés comme
    répétés et strippés. Sans cette normalisation, chaque numéro serait
    unique et le stripper les laisserait passer."""
    pdf = tmp_path / "doc.pdf"
    header = "Guide ANSSI"
    _make_pdf(
        pdf,
        [
            [header, "content A", "14"],
            [header, "content B", "15"],
            [header, "content C", "16"],
            [header, "content D", "17"],
        ],
    )
    pages = extract_pages(pdf, doc_id="x")
    for p in pages:
        assert header not in p.text
        for num in ("14", "15", "16", "17"):
            assert num not in p.text


def test_extract_pages_small_doc_not_stripped(tmp_path):
    """Docs < MIN_PAGES_FOR_STRIP pages : rien n'est strippé.
    Un header présent sur 2/2 pages resterait sinon stripped à 100%
    alors que le doc est trop petit pour trancher."""
    pdf = tmp_path / "doc.pdf"
    header = "Small guide"
    _make_pdf(
        pdf,
        [
            [header, "body 1"],
            [header, "body 2"],
        ],
    )
    pages = extract_pages(pdf, doc_id="x")
    for p in pages:
        assert header in p.text


# --- erreurs typées ---


def test_extract_pages_missing_pdf_raises(tmp_path):
    with pytest.raises(PdfMissingError, match=r"doc42.*absent"):
        extract_pages(tmp_path / "nope.pdf", doc_id="doc42")


def test_extract_pages_expected_page_count_ok(tmp_path):
    pdf = tmp_path / "d.pdf"
    _make_pdf(pdf, [["a"], ["b"], ["c"]])
    pages = extract_pages(pdf, doc_id="d", expected_page_count=3)
    assert len(pages) == 3


def test_extract_pages_expected_page_count_mismatch_raises(tmp_path):
    """REQ-CORPUS-02 — invariant page ≤ pages(doc) violé."""
    pdf = tmp_path / "d.pdf"
    _make_pdf(pdf, [["a"], ["b"], ["c"]])
    with pytest.raises(PageCountMismatchError, match="REQ-CORPUS-02"):
        extract_pages(pdf, doc_id="d", expected_page_count=5)


def test_extract_pages_empty_extraction_raises(tmp_path, monkeypatch):
    """PDF valide mais 0 texte (scan brut) → surface, ne silence pas.

    Monkeypatche ``extract_text`` pour forcer le retour vide sans
    fabriquer un vrai PDF scanné.
    """
    pdf = tmp_path / "d.pdf"
    _make_pdf(pdf, [["a"], ["b"]])
    monkeypatch.setattr(
        pdfplumber.page.Page,
        "extract_text",
        lambda self, **kw: "",
    )
    with pytest.raises(EmptyExtractionError, match="zero characters"):
        extract_pages(pdf, doc_id="d")


# --- _normalize_line ---


def test_normalize_line_replaces_digit_runs():
    """Digits collapse to '#', edge '#' + spaces get trimmed so that the
    page-number marker migrating between line start and end (paires vs
    impaires) does not defeat the noise detector."""
    assert _normalize_line("Page 14 de 72") == "page # de"


def test_normalize_line_lowercases_and_strips():
    assert _normalize_line("  HELLO World  ") == "hello world"


def test_normalize_line_empty_stays_empty():
    assert _normalize_line("   ") == ""


def test_normalize_line_trims_edge_page_number_marker():
    """Le marqueur '#' migre du début à la fin selon parité de page — les
    deux formes doivent hasher identiquement pour être détectées ≥50%."""
    assert _normalize_line("16– RECOMMANDATIONS") == _normalize_line(
        "RECOMMANDATIONS –17"
    )


def test_normalize_line_collapses_dash_variants():
    """Différents tirets (ASCII, en-dash, em-dash) doivent être équivalents."""
    assert _normalize_line("A - B") == _normalize_line("A – B") == _normalize_line("A — B")


# --- _is_page_number_only ---


def test_is_page_number_only_true_cases():
    for s in ["14", "  14  ", "14–17", "14 - 17", "  ", "12."]:
        if s.strip():
            assert _is_page_number_only(s), f"{s!r} should be page-number-only"


def test_is_page_number_only_false_cases():
    for s in ["Page 14", "14 mesures", "chapitre 1"]:
        assert not _is_page_number_only(s), f"{s!r} should NOT be page-number-only"


def test_is_page_number_only_blank_line_false():
    """Une ligne vide n'est PAS un numéro de page — la garde ``bool(line.strip())``
    l'exclut, sinon on strip aussi les blancs et on massacre la mise en page."""
    assert not _is_page_number_only("")
    assert not _is_page_number_only("   ")


# --- _strip_noise unit ---


def test_strip_noise_removes_repeated_line():
    pages = ["header\nA", "header\nB", "header\nC", "header\nD"]
    out = _strip_noise(pages)
    assert all("header" not in p for p in out)
    assert out == ["A", "B", "C", "D"]


def test_strip_noise_preserves_when_below_min_pages():
    pages = ["header\nA", "header\nB"]
    assert _strip_noise(pages) == pages


def test_strip_noise_empty_input():
    assert _strip_noise([]) == []


def test_strip_noise_threshold_boundary():
    """3/4 = 75% ≥ 50% : strippé. 1/4 = 25% : gardé.

    Suffixes non-digits (a, b, c, d) sur les 'unique' pour éviter que
    la normalisation digits→# ne les fasse tous collapser sur la même
    clef.
    """
    pages = [
        "repeated\nunique_a",
        "repeated\nunique_b",
        "repeated\nunique_c",
        "other\nunique_d",  # 'repeated' absent ici
    ]
    out = _strip_noise(pages)
    for p in out:
        assert "repeated" not in p
    assert any("other" in p for p in out)  # 1/4 : gardé
    for tag in ("a", "b", "c", "d"):
        assert any(f"unique_{tag}" in p for p in out)


def test_strip_noise_intra_page_repetition_counts_once():
    """Une ligne présente 3× sur la même page compte comme 1 apparition.
    Le signal est la répétition cross-page, pas intra-page."""
    pages = [
        "spam\nspam\nspam\nA",
        "B",
        "C",
    ]
    out = _strip_noise(pages)
    # 'spam' : 1 page sur 3 = 33% < 50% → gardé
    assert "spam" in out[0]


def test_strip_noise_at_min_pages_threshold_boundary():
    """Frontière ``MIN_PAGES_FOR_STRIP`` : à 2 pages skip (test existant),
    à 3 pages exactement le strip se déclenche. Falsifie la constante,
    pas seulement le comportement au-dessus."""
    assert MIN_PAGES_FOR_STRIP == 3, "test attaché à la valeur documentée"
    pages_at_threshold = [
        "footer\nunique_A",
        "footer\nunique_B",
        "footer\nunique_C",
    ]
    out = _strip_noise(pages_at_threshold)
    assert all("footer" not in p for p in out), (
        "à MIN_PAGES_FOR_STRIP pages, le strip doit s'activer"
    )


# --- fixture reportlab : reproduit le pattern footer alternant
# ---   (CI-safe équivalent du test d'intégration AD)


def test_extract_pages_strips_footer_with_alternating_page_number_position(
    tmp_path,
):
    """Reproduit le pattern ANSSI où le numéro de page migre du début
    de la ligne footer (pages paires) à la fin (impaires) :
    "16– RECOMMANDATIONS…" vs "RECOMMANDATIONS… –17".

    ``n_pages = 9`` choisi **asymétrique** exprès (4 pages paires vs 5
    impaires) pour que le test falsifie strictement le mécanisme
    ``_EDGE_STRIP`` :

    - Sans edge-strip : les deux variantes hashent différemment
      (``# footer`` vs ``footer #``). Ratios 4/9 = 44% et 5/9 = 56%.
      Une variante minoritaire passe sous le seuil ⇒ 4 pages gardent
      leur footer parasite. Test échoue.
    - Avec edge-strip : les ``#`` en bord sont trimés, les 9 lignes
      hashent identiquement ⇒ 9/9 = 100% ⇒ strippé partout. Test passe.

    Ce test est le pendant CI-safe de
    ``test_extract_pages_ad_real_pdf_strips_footer`` (skipif corpus
    réel).
    """
    pdf = tmp_path / "doc.pdf"
    footer_body = "RECOMMANDATIONS RELATIVES"
    n_pages = 9  # asymétrique : 4 paires vs 5 impaires
    pages_content = []
    for i, page_no in enumerate(range(1, n_pages + 1)):
        # Corps unique par lettre (pas de digit → pas de collapse en '#'
        # via la normalisation).
        body = f"corps unique {chr(ord('a') + i)}"
        if page_no % 2 == 0:
            footer_line = f"{page_no}- {footer_body}"
        else:
            footer_line = f"{footer_body} -{page_no}"
        pages_content.append([body, footer_line])
    _make_pdf(pdf, pages_content)
    pages = extract_pages(pdf, doc_id="fx")
    residual = sum(1 for p in pages if footer_body in p.text)
    assert residual == 0, (
        f"Footer '{footer_body}' subsiste sur {residual}/{n_pages} pages — "
        f"le trim edge '#' ne joue pas son rôle."
    )
    # Corps préservé.
    for i in range(n_pages):
        assert f"corps unique {chr(ord('a') + i)}" in pages[i].text


# --- extract_doc wrapper ---


def _write_manifest(path: Path, docs: list[dict]) -> None:
    path.write_text(yaml.safe_dump({"documents": docs}), encoding="utf-8")


def test_load_manifest_returns_dict_with_documents_key(tmp_path):
    manifest_path = tmp_path / "manifest.yaml"
    _write_manifest(
        manifest_path,
        [{"doc_id": "d", "filename": "d.pdf", "sha256": "x"}],
    )
    m = load_manifest(manifest_path)
    assert isinstance(m, dict)
    assert "documents" in m
    assert m["documents"][0]["doc_id"] == "d"


def test_extract_doc_unknown_doc_id_raises(tmp_path):
    manifest_path = tmp_path / "manifest.yaml"
    _write_manifest(
        manifest_path,
        [{"doc_id": "known", "filename": "known.pdf", "sha256": "x", "pages": 1}],
    )
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    manifest = load_manifest(manifest_path)
    with pytest.raises(UnknownDocIdError, match=r"not declared"):
        extract_doc(manifest, "missing", pdf_dir)


def test_extract_doc_resolves_manifest_entry(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _make_pdf(pdf_dir / "myguide.pdf", [["a"], ["b"], ["c"]])
    manifest_path = tmp_path / "manifest.yaml"
    _write_manifest(
        manifest_path,
        [
            {
                "doc_id": "myguide",
                "filename": "myguide.pdf",
                "sha256": "x",
                "pages": 3,
            }
        ],
    )
    pages = extract_doc(load_manifest(manifest_path), "myguide", pdf_dir)
    assert len(pages) == 3
    assert all(p.doc_id == "myguide" for p in pages)


def test_extract_doc_enforces_manifest_page_count(tmp_path):
    """REQ-CORPUS-02 propagé via le wrapper manifest."""
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _make_pdf(pdf_dir / "d.pdf", [["a"], ["b"]])
    manifest_path = tmp_path / "manifest.yaml"
    _write_manifest(
        manifest_path,
        [{"doc_id": "d", "filename": "d.pdf", "sha256": "x", "pages": 999}],
    )
    with pytest.raises(PageCountMismatchError):
        extract_doc(load_manifest(manifest_path), "d", pdf_dir)


def test_extract_doc_without_pages_field_skips_req_02_check(tmp_path):
    """Manifest sans champ ``pages`` → REQ-CORPUS-02 non enforced sur ce
    doc, mais l'extraction ne doit pas casser pour autant. Guard contre
    un tightening qui casserait la rétrocompatibilité manifest."""
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _make_pdf(pdf_dir / "d.pdf", [["a"], ["b"]])
    manifest_path = tmp_path / "manifest.yaml"
    _write_manifest(
        manifest_path,
        [{"doc_id": "d", "filename": "d.pdf", "sha256": "x"}],
    )
    pages = extract_doc(load_manifest(manifest_path), "d", pdf_dir)
    assert len(pages) == 2


# --- exception typing (REQ-CORPUS-02) ---


def test_page_count_mismatch_is_catchable_as_corpus_error(tmp_path):
    """L'événement 'PDF n'est pas celui gelé au manifest' est un défaut
    du contrat corpus au même titre que sha256/bytes — un consommateur
    VCD qui écrit ``except CorpusError`` doit l'attraper."""
    pdf = tmp_path / "d.pdf"
    _make_pdf(pdf, [["a"]])
    with pytest.raises(CorpusError):
        extract_pages(pdf, doc_id="d", expected_page_count=42)


def test_page_count_mismatch_is_catchable_as_extraction_error(tmp_path):
    """Symétriquement, un pipeline caller qui filtre les erreurs
    d'extraction via ``except ExtractionError`` doit l'attraper aussi."""
    pdf = tmp_path / "d.pdf"
    _make_pdf(pdf, [["a"]])
    with pytest.raises(ExtractionError):
        extract_pages(pdf, doc_id="d", expected_page_count=42)


# --- intégration sur un vrai PDF ANSSI ---


@pytest.mark.skipif(
    not (REAL_PDFS_DIR / "reco-active-directory.pdf").exists(),
    reason="Real corpus PDFs absent (git-ignored, run download_corpus.py first)",
)
def test_extract_pages_ad_real_pdf_strips_footer():
    """Sanity check sur le vrai PDF Active Directory (166 pages).
    Le footer typique 'RECOMMANDATIONSRELATIVES…ACTIVEDIRECTORY' doit
    disparaître de la vaste majorité des pages après stripping.

    Le pattern est déjà couvert en CI par
    :func:`test_extract_pages_strips_footer_with_alternating_page_number_position`
    (fixture reportlab). Ce test-ci le confronte à la matière réelle,
    en local — doit être exécuté avant merge et l'output collé dans
    la PR description (voir README § Development workflow).
    """
    pages = extract_pages(
        REAL_PDFS_DIR / "reco-active-directory.pdf",
        doc_id="active-directory",
    )
    assert len(pages) > 100
    marker = "RECOMMANDATIONSRELATIVES"
    with_marker = sum(1 for p in pages if marker in p.text)
    assert with_marker < len(pages) * 0.05, (
        f"Footer '{marker}' subsiste sur {with_marker}/{len(pages)} pages "
        f"(tolérance : < 5%)."
    )


@pytest.mark.skipif(
    not (REAL_PDFS_DIR / "guide-hygiene.pdf").exists(),
    reason="Real corpus PDFs absent (git-ignored, run download_corpus.py first)",
)
def test_extract_pages_hygiene_documented_limit_current_behavior():
    """Sentinelle du cas dégradé documenté : sur ``guide-hygiene.pdf``
    le header 'Guide d'hygiène informatique' est fusionné avec la 1re
    ligne de contenu, hors de portée du repetition detector.

    Fige la mesure du header résiduel :
    - baseline mesurée le **2026-07-03** avec pdfplumber 0.11.10 :
      45/72 pages (62.5%) contaminées.

    Le test échoue **dans les deux sens** :
    - à la hausse : régression, on s'est mis à laisser passer plus de
      bruit qu'avant.
    - à la baisse : super — un fix ciblé a marché, mettre à jour
      ``HYGIENE_BASELINE_MAX`` et la doc pour tracer le progrès.

    L'assertion ``contaminated > 0`` en fin garantit qu'on ne peut pas
    silencieusement croire "la limite est levée" alors que c'est le
    marker qui a bougé — obligeant l'auteur du fix à remettre à jour
    la sentinelle explicitement.
    """
    pages = extract_pages(
        REAL_PDFS_DIR / "guide-hygiene.pdf", doc_id="hygiene"
    )
    marker = "Guide d’hygiène informatique"  # apostrophe typographique
    contaminated = sum(1 for p in pages if marker in p.text)
    HYGIENE_BASELINE_MAX = 46  # actuel 45 ; +1 marge variance de version pdfplumber.
    assert contaminated <= HYGIENE_BASELINE_MAX, (
        f"{contaminated}/{len(pages)} pages contiennent encore le header "
        f"parasite (baseline max {HYGIENE_BASELINE_MAX}). Régression : "
        f"le stripping s'est dégradé."
    )
    assert contaminated > 0, (
        "Le cas dégradé hygiene semble résolu : mettre à jour la "
        "baseline, retirer la limite dans la doc de extract_pdf.py, "
        "et ce test doit devenir une garde de non-régression stricte."
    )
