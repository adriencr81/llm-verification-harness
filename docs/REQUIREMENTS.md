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

**Statut** — *enforced at Page boundary*. Le chunk-side reste
*pending* jusqu'à la livraison du chunking (lot suivant Brique 1), qui
héritera de l'invariant par construction (`chunk.page_num ==
page.page_num` sans réouverture du PDF). Le passage à *fully enforced*
sera formalisé quand `chunks.json` sera produit.

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
