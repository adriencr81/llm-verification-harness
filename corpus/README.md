# Corpus ANSSI — `llm-verification-harness`

Le corpus qui alimentera le RAG et le banc de vérification (Brique 2+).
11 guides ANSSI figés par empreinte cryptographique dans `manifest.yaml`.

## Intention IVVQ

Règle : **toute modification silencieuse d'un PDF est refusée par construction**.
`download_corpus.py` vérifie que chaque fichier sur disque correspond au SHA256
déclaré dans le manifest ; toute divergence sort en code d'erreur non-zéro avec
identification du doc fautif. Le manifest est un **contrat écrit**, ce script est
sa **vérification automatique**. Ensemble ils matérialisent la propriété
"corpus verrouillé par empreinte cryptographique" citée en VCD (Brique 7).

Champs sous contrat par doc (détaillés en tête de `manifest.yaml`) :

- `sha256` — empreinte cryptographique du fichier (non-altération binaire).
- `bytes` — taille sur disque en octets (sanity check amont).
- `pages` — nombre de pages du PDF (invariant `page_ref <= pages` exploité
  par la vérification de provenance des chunks — Brique 2/3).

Le `doc_id` (slug immuable) est la seule clé stable entre le manifest, les
chunks, les cas de test et le VCD. Renommer un `filename` ne casse aucune
traçabilité.

## Utilisation

**Prérequis** — installer les dépendances (les tests utilisent notamment
`reportlab` pour générer des PDFs de fixture) :

```
pip install -r requirements.txt
```

Vérifier / (re)télécharger le corpus (11 PDFs attendus dans `pdfs/`) :

```
python download_corpus.py
```

Sortie 0 = tous les PDFs sont conformes au manifest. Sortie non-zéro =
au moins un `sha256` diverge ou un doc `signed_url: true` est absent (à
récupérer manuellement depuis sa `landing_page`).

Enrichir le manifest avec `bytes` et `pages` après un bump du corpus :

```
python enrich_manifest.py
```

Idempotent : "Rien à faire : manifest déjà enrichi." si toutes les entrées
portent déjà ces champs.

## Processus de mise à jour ("bump conscient")

Un doc n'est jamais remplacé silencieusement. La procédure est délibérément
verbeuse — "impossible d'accidenter un bump" est plus important que la
vélocité de mise à jour :

1. Placer le nouveau PDF dans `pdfs/<filename>`.
2. Recalculer son SHA256 et l'inscrire dans `manifest.yaml`.
3. Effacer `bytes` et `pages` de l'entrée modifiée.
4. `python enrich_manifest.py` — recalcule les champs manquants.
5. `python download_corpus.py` — vérifie que le nouveau hash tient.
6. Committer avec un message explicite : `corpus — bump <doc_id> vers <version>`.

## Licence & traçabilité amont

Contenu source : guides publiés par l'ANSSI sous **Licence Ouverte 2.0
(Etalab)** — voir la racine du manifest (`license`).

Sémantique des URLs de traçabilité :

- `download_url` — URL directe ANSSI, stable, téléchargement automatique.
- `landing_page` — page humaine ANSSI, pour fallback manuel et citation VCD.
- `signed_url: true` — 3 docs derrière S3 présigné OVH (`sf-cyber`, expiration
  ~1h). `download_url` est `null`, téléchargement manuel obligatoire depuis
  la `landing_page`.

## Tests

Tests unitaires liés au corpus :

- `tests/test_download_corpus.py` — validité du contrat de non-altération
  (dont `test_single_byte_alteration_raises_integrity_error`, cité au VCD
  Brique 7 comme preuve de falsifiabilité).
- `tests/test_enrich_manifest.py` — 6 tests couvrant enrichissement,
  idempotence bit-à-bit, préservation d'en-tête, unicité sha256, atomicité
  d'écriture, rejet des lookalikes du matcher.

```
python -m pytest -q
```
