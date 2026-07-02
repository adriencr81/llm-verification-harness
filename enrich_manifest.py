"""Enrichit corpus/manifest.yaml avec `bytes` et `pages` par doc — one-shot Brique 1.

Motivation IVVQ (voir docs/REQUIREMENTS.md, cités par le VCD Brique 7) :
    `sha256` [REQ-CORPUS-01] seul garantit la non-altération binaire du
    PDF. Il ne donne aucune prise pour croiser la sortie de l'extraction
    (Brique 2). En ajoutant `pages` [REQ-CORPUS-02] au manifest, on
    obtient un invariant vérifiable : tout chunk portant provenance
    `(doc_id, page=N)` doit satisfaire N <= pages du doc — sinon erreur
    de provenance détectée immédiatement, sans avoir à réouvrir le PDF.

    `bytes` [REQ-CORPUS-03] est un sanity check amont léger — fichier
    tronqué ou mauvais fichier déposé manuellement pour les 3 docs
    signed_url. Vérifié par `download_corpus.verify_document` en préambule
    au check SHA256, via l'exception `CorpusSizeError`.

Ces valeurs sont gelées comme le SHA256 : script à ne re-jouer qu'en cas
de bump conscient du corpus (nouveau doc, remplacement d'un doc).

Insertion surgicale (pyyaml en lecture, texte en écriture) : les
commentaires d'en-tête du manifest — qui documentent le contrat
IVVQ — sont préservés octet pour octet. Précondition d'unicité des sha256
vérifiée en amont : sans elle, l'insertion par match de hash pourrait
cibler un doc arbitraire en silence. Écriture atomique via `.tmp` +
`replace` — cohérent avec `download_corpus.fetch`.

Usage :
    python enrich_manifest.py

Prérequis : les 11 PDFs présents dans corpus/pdfs/ (cf. download_corpus.py).
Exit 0 si succès (ou rien à faire), 1 si un PDF manque.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pdfplumber
import yaml

REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = REPO_ROOT / "corpus" / "manifest.yaml"
PDF_DIR = REPO_ROOT / "corpus" / "pdfs"

# Match strict : uniquement une ligne "  sha256: <hex64>" (avec indent
# capturé). Refuse `sha256_source:`, `# sha256: ...`, ou toute variante
# de forme qui pourrait déclencher une insertion à la mauvaise place.
SHA_LINE_RE = re.compile(
    r"^(?P<indent>\s+)sha256:\s+(?P<hash>[0-9a-f]{64})\s*$"
)


def count_pages(pdf_path: Path) -> int:
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


def enrich(manifest_path: Path, pdf_dir: Path) -> int:
    with manifest_path.open("r", encoding="utf-8", newline="") as f:
        raw = f.read()
    manifest = yaml.safe_load(raw)
    documents = manifest["documents"]

    # Précondition d'identité binaire du manifest : chaque doc a un sha256
    # unique. Si deux docs partagent le même hash, l'insertion par match
    # de hash ci-dessous pourrait viser un doc arbitraire — dérive
    # silencieuse. On refuse d'écrire tant que la précondition ne tient
    # pas. Levée avant tout accès disque des PDFs.
    hashes = [d["sha256"] for d in documents]
    if len(set(hashes)) != len(hashes):
        raise ValueError(
            f"sha256 non unique dans {manifest_path.name} — "
            "identité binaire du manifest violée, refus d'enrichir."
        )

    to_enrich = [
        d for d in documents
        if "bytes" not in d or "pages" not in d
    ]
    if not to_enrich:
        print("Rien à faire : manifest déjà enrichi.")
        return 0

    stats: dict[str, tuple[int, int]] = {}
    for doc in to_enrich:
        pdf_path = pdf_dir / doc["filename"]
        if not pdf_path.exists():
            print(
                f"[MISS] {doc['doc_id']} : PDF absent ({pdf_path}). "
                "Lance d'abord download_corpus.py.",
                file=sys.stderr,
            )
            return 1
        n_bytes = pdf_path.stat().st_size
        n_pages = count_pages(pdf_path)
        stats[doc["doc_id"]] = (n_bytes, n_pages)
        print(f"[OK]   {doc['doc_id']:20s} {n_bytes:>10,} bytes  {n_pages:>4} pages")

    newline = "\r\n" if "\r\n" in raw else "\n"
    by_hash = {d["sha256"]: d for d in documents}

    out: list[str] = []
    inserted = 0
    for line in raw.splitlines(keepends=True):
        out.append(line)
        m = SHA_LINE_RE.match(line)
        if not m:
            continue
        doc = by_hash.get(m.group("hash"))
        if doc is None:
            continue
        if "bytes" in doc and "pages" in doc:
            # Doc déjà enrichi lors d'un run précédent partiel — skip.
            continue
        indent = m.group("indent")
        n_bytes, n_pages = stats[doc["doc_id"]]
        out.append(f"{indent}bytes: {n_bytes}{newline}")
        out.append(f"{indent}pages: {n_pages}{newline}")
        inserted += 1

    # Écriture atomique : un crash entre l'ouverture et writelines()
    # laisserait sinon le manifest tronqué. Pattern aligné sur
    # download_corpus.fetch.
    tmp_path = manifest_path.with_name(manifest_path.name + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8", newline="") as f:
            f.writelines(out)
        tmp_path.replace(manifest_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    print(f"\n{inserted} entrée(s) enrichie(s) dans {manifest_path.name}.")
    return 0


def main() -> int:
    return enrich(MANIFEST_PATH, PDF_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
