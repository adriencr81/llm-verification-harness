# Requirements — `llm-verification-harness`

Registre provisoire des exigences en amont du corpus et du banc de
vérification. Ce registre est **fluide jusqu'à la Brique 5**, où il sera
gelé comme spec formelle des cas de test. Tout identifiant `REQ-*` cité
dans un docstring ou un test doit avoir une entrée ici + au moins un
consommateur code référencé **ou un consommateur prévu, brique cible
citée**.

## Corpus (`REQ-CORPUS-*`)

### `REQ-CORPUS-01` — Non-altération binaire (SHA256)

Chaque PDF du corpus est identifié par son empreinte SHA256 dans
`corpus/manifest.yaml`. Toute divergence entre le contenu sur disque et
le hash déclaré doit être détectée et refusée sans intervention manuelle.

- **Producteur** : `corpus/manifest.yaml` (SHA256 rempli au dépôt initial)
- **Consommateur** : `download_corpus.verify_document`
- **Test de falsifiabilité** : `tests/test_download_corpus.py::test_single_byte_alteration_raises_integrity_error`
- **Exception** : `CorpusIntegrityError`

### `REQ-CORPUS-02` — Invariant de provenance (`pages`)

Toute déclaration de provenance de chunk `(doc_id, page=N)` produite par
l'extraction (Brique 2) et le chunking (Brique 3) doit satisfaire
`N <= pages(doc)` du manifest, sans réouverture du PDF. L'exigence
transitive : le champ `pages` du manifest est gelé au même titre que
`sha256`.

- **Producteur** : `enrich_manifest.py` (fige `pages` par lecture pdfplumber)
- **Consommateurs (à venir)** : module d'extraction, module de chunking
- **Test** : (à venir Brique 2 — vérification côté chunk)

### `REQ-CORPUS-03` — Sanity check de taille (`bytes`)

Quand `bytes` est déclaré dans le manifest, le fichier sur disque doit
avoir exactement cette taille. Check exécuté **avant** SHA256 par
`verify_document` : cheap sanity check, utile en particulier pour les 3
docs `signed_url: true` dont le téléchargement est manuel — un fichier
tronqué ou un mauvais fichier déposé est détecté sans passer par le
hachage complet (coûteux).

Le check est **opt-in** : un doc sans `bytes` au manifest passe sans
lever, ce qui permet à un `enrich_manifest.py` partiel (ajout récent, pas
encore enrichi) de ne pas casser la vérification amont.

- **Producteur** : `enrich_manifest.py` (fige `bytes` par `os.path.getsize`)
- **Consommateur** : `download_corpus.verify_document`
- **Test** : `tests/test_download_corpus.py::test_declared_bytes_mismatch_raises_size_error`
- **Exception** : `CorpusSizeError`
- **Type attendu** : `int` — dette de validation de schéma (une valeur
  YAML mal typée en string glisserait aujourd'hui sans erreur claire).
  Levée par la validation de schéma manifest (Brique 5).

## Statut

**Provisoire.** Registre définitif = Brique 5 (spec des cas de test du
banc de vérification). Les IDs actuels sont considérés stables : ils
seront **au minimum** conservés en Brique 5, éventuellement complétés
par les exigences propres au banc lui-même (`REQ-BENCH-*`), à la
génération de réponses (`REQ-GEN-*`), et au VCD (`REQ-VCD-*`).
