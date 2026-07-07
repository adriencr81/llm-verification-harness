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
l'extraction et le chunking doit satisfaire `N <= pages(doc)` du
manifest, sans réouverture du PDF. L'exigence transitive : le champ
`pages` du manifest est gelé au même titre que `sha256`.

**Enforcement** — `extract_pdf.extract_doc` refuse d'émettre des Pages
si le count réel du PDF diverge du `pages` déclaré au manifest. La
vérification est une **égalité stricte** (`actual == manifest.pages`).
Cette égalité stricte **implique** l'invariant `N <= pages(doc)` par
construction sur tout chunk issu de ces Pages : puisque `page_num`
est produit par énumération 1-indexée de `[1, len(pages)]` et que
`len(pages) == manifest.pages`, alors `N <= manifest.pages` tient sans
vérification chunk-side redondante.

**Statut** — *enforced at Page boundary AND persisted at rest*. Le
count `manifest.pages == len(pages)` est vérifié à l'extraction
(`extract_pdf.extract_doc`), puis matérialisé dans
`corpus/pages.jsonl` (voir `REQ-CORPUS-04`) — une régression du
count est détectable au diff, sans réouvrir un PDF. Le chunk-side
reste *pending* jusqu'à la livraison du chunking (lot suivant
Brique 1), qui lira `pages.jsonl` (et non pdfplumber) et héritera
de l'invariant par construction (`chunk.page_num == page.page_num`).
Le passage à *fully enforced* sera formalisé quand `chunks.jsonl`
sera produit.

**Paramètre pipeline associé** — `extract_pdf.NOISE_THRESHOLD = 0.5`
gouverne la détection header/footer par répétition (une ligne dont le
hash normalisé apparaît sur ≥ 50% des pages est retirée). Valeur
empiriquement défendable : le motif corps le plus fréquent hors bruit
sur EBIOS-RM (le doc le plus contaminé) reste sous ~37% — sous le
seuil, préservé. Cible du VCD Brique 7 comme paramètre traçable, pas
comme constante gravée.

- **Producteur** : `enrich_manifest.py` (fige `pages` par lecture pdfplumber)
- **Consommateur (extraction)** : `extract_pdf.extract_doc` — enforced
- **Consommateur (chunking)** : à venir (lot suivant Brique 1)
- **Tests amont** :
  - `tests/test_extract_pdf.py::test_extract_pages_expected_page_count_mismatch_raises`
  - `tests/test_extract_pdf.py::test_extract_doc_enforces_manifest_page_count`
  - `tests/test_extract_pdf.py::test_page_count_mismatch_is_catchable_as_corpus_error`
- **Test-sentinelle cas dégradé** : `tests/test_extract_pdf.py::test_extract_pages_hygiene_documented_limit_current_behavior`
  (fige la baseline 45/72 pages contaminées sur `guide-hygiene.pdf`)
- **Exception** : `extract_pdf.PageCountMismatchError` — héritage
  multiple `(CorpusError, ExtractionError)`, catchable des deux côtés
  (contrat corpus ET pipeline extraction), voir son docstring.

### `REQ-CORPUS-04` — Baseline gelée de l'extraction (`pages.jsonl`)

L'extraction PDF→texte est persistée dans `corpus/pages.jsonl`
(versionné) et son SHA256 est gelé au manifest sous
`derived_artifacts.pages_jsonl.sha256`. Toute régression silencieuse
du pipeline d'extraction — nouvelle version de pdfplumber, changement
de seuil dans `_strip_noise`, mauvaise gestion d'un cas particulier,
**y compris une dérive text-only qui préserverait les counts et
l'ordre** — est détectée par machine (test SHA256), sans réouvrir un
PDF ni dépendre d'un `git diff` humain.

**Motivation** — le chunker (lot suivant Brique 1) consomme
`pages.jsonl` et non pdfplumber. Une extraction stable = un
chunking reproductible = un banc IVVQ auditable en aval sans
dépendance à la machine qui a fait tourner l'extraction.

**Format** — JSONL, une ligne = une page, clés
`(doc_id, page_num, text)` dans cet ordre, `ensure_ascii=False`
(accents français natifs → diffs lisibles), ordre : documents
selon le manifest, pages 1-indexées par doc. Fins de ligne LF
figées par `.gitattributes` (invariance plateforme du contrat
bit-for-bit).

**Statut** — *enforced*. Contrat SHA256 symétrique à REQ-CORPUS-01
(côté PDF) : le pipeline est déterministe, le fichier est committé,
le hash est déclaré au manifest, une divergence est refusée par le
test. Détection de dérive contenu = machine, pas humain.

**Canal de détection de régression** — deux canaux redondants :
1. **Machine (CI)** — `test_baseline_hash_matches_manifest` compare
   le SHA256 calculé au SHA256 déclaré. Une divergence fait échouer
   la suite. C'est le contrôle *primaire*.
2. **Humain (PR review)** — `git diff corpus/pages.jsonl` reste
   lisible (`ensure_ascii=False`) et permet au reviewer de qualifier
   le changement (bump délibéré vs régression) avant approbation.

- **Producteur** : `extract_all.extract_all`
- **Consommateur (chunking)** : à venir (lot suivant Brique 1)
- **Tests amont (baseline)** :
  - `tests/test_extract_all.py::test_baseline_hash_matches_manifest` (**primaire**)
  - `tests/test_extract_all.py::test_baseline_uses_lf_line_endings_only`
  - `tests/test_extract_all.py::test_baseline_is_valid_jsonl_with_expected_keys`
  - `tests/test_extract_all.py::test_baseline_covers_every_document_in_manifest`
  - `tests/test_extract_all.py::test_baseline_page_count_per_doc_matches_manifest`
  - `tests/test_extract_all.py::test_baseline_page_num_is_1_indexed_and_contiguous_per_doc`
  - `tests/test_extract_all.py::test_baseline_preserves_manifest_document_order`
- **Test de déterminisme** :
  `tests/test_extract_all.py::test_extract_all_second_run_is_bit_for_bit_identical`
- **Producer env pinné** : `pdfplumber` sous
  `derived_artifacts.pages_jsonl.producer_env` (une regénération sous
  une version différente casse le SHA256 délibérément).

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
