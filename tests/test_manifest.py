"""Tests du contrat d'identité stable du manifest ANSSI.

Le manifest promet en en-tête (section ``# Identité stable``) que ``doc_id``
est un **slug court immuable** — c'est LUI qui identifie un document dans
les chunks (Brique 2/3), les cas de test (Brique 5) et le VCD (Brique 7),
pas le filename. Un renommage de filename ne doit jamais casser la
traçabilité amont ; un renommage de ``doc_id`` doit être un acte
délibéré, visible en revue de code.

Aujourd'hui cette promesse est écrite, pas testée. Ces tests la
matérialisent :

- ``test_doc_ids_match_frozen_baseline`` : le set des ``doc_id`` du
  manifest doit correspondre exactement à ``EXPECTED_DOC_IDS``. Toute
  dérive (renommage, ajout, suppression) fait échouer le test — la
  baseline se met à jour dans le MÊME commit que la modification
  correspondante du manifest.
- ``test_doc_ids_are_unique`` : garde contre un doublon accidentel de
  ``doc_id`` qui serait masqué par la conversion en set du test
  précédent (deux entrées avec la même ``doc_id`` = un chunk pointerait
  vers un doc arbitraire au lookup).

Ces tests sont des **invariants d'implémentation** au même titre que les
sentinelles d'``enrich_manifest.py`` (idempotence bit-à-bit, atomicité
d'écriture, matcher regex). Ils ne correspondent pas à un ``REQ-CORPUS-*``
propre — ils garantissent la stabilité de la clé de jointure sur laquelle
les REQ-CORPUS-02+ vont s'appuyer.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "corpus" / "manifest.yaml"

# Baseline gelée des ``doc_id`` du corpus. Contrat : ce set est édité
# dans le MÊME commit qu'une modification correspondante de
# corpus/manifest.yaml (ajout, suppression, renommage). Bump conscient
# — analogue au bump SHA256 de download_corpus.py.
EXPECTED_DOC_IDS: frozenset[str] = frozenset(
    {
        "active-directory",
        "admin-si",
        "cartographie",
        "docker",
        "ebios-rm",
        "hygiene",
        "mfa",
        "nomadisme",
        "pacs",
        "secnumcloud",
        "sites-web",
    }
)


def _load_documents() -> list[dict]:
    with MANIFEST_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)["documents"]


def test_doc_ids_match_frozen_baseline() -> None:
    actual = {doc["doc_id"] for doc in _load_documents()}
    unexpected = actual - EXPECTED_DOC_IDS
    missing = EXPECTED_DOC_IDS - actual
    assert actual == EXPECTED_DOC_IDS, (
        f"Dérive du set des doc_id — "
        f"inattendus : {sorted(unexpected)}, absents : {sorted(missing)}. "
        f"Si le changement est délibéré, éditer EXPECTED_DOC_IDS dans "
        f"tests/test_manifest.py DANS LE MÊME commit."
    )


def test_doc_ids_are_unique() -> None:
    doc_ids = [doc["doc_id"] for doc in _load_documents()]
    assert len(doc_ids) == len(set(doc_ids)), (
        f"doc_id dupliqué dans le manifest — un chunk pointerait vers "
        f"un doc arbitraire au lookup. Liste : {sorted(doc_ids)}"
    )
