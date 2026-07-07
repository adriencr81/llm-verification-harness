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

**Statut** — *fully enforced*. Trois niveaux qui composent :

1. **Page boundary** — `extract_pdf.extract_doc` vérifie l'égalité
   stricte `actual == manifest.pages` à l'extraction. Enforced.
2. **Persistance** — `corpus/pages.jsonl` est versionné et gèle
   l'ordre + les `page_num` 1-indexés (voir REQ-CORPUS-04). Une
   régression du count est détectable au diff sans réouvrir de PDF.
3. **Chunk boundary** — `chunk_pages.chunk_page(text, doc_id, page_num)`
   copie `page_num` verbatim de la Page source dans chaque Chunk émis
   et n'appelle jamais un pdfplumber ni ne modifie ce champ. Puisque
   Page.page_num ∈ [1, manifest.pages] par construction (via (1)+(2)),
   Chunk.page_num ∈ [1, manifest.pages] par transitivité. Vérifié
   sur le corpus réel par
   `tests/test_chunk_pages.py::test_baseline_no_chunk_crosses_page_boundary_on_real_corpus`.

**Paramètre pipeline associé** — `extract_pdf.NOISE_THRESHOLD = 0.5`
gouverne la détection header/footer par répétition (une ligne dont le
hash normalisé apparaît sur ≥ 50% des pages est retirée). Valeur
empiriquement défendable : le motif corps le plus fréquent hors bruit
sur EBIOS-RM (le doc le plus contaminé) reste sous ~37% — sous le
seuil, préservé. Cible du VCD Brique 7 comme paramètre traçable, pas
comme constante gravée.

- **Producteur** : `enrich_manifest.py` (fige `pages` par lecture pdfplumber)
- **Consommateur (extraction)** : `extract_pdf.extract_doc` — enforced
- **Consommateur (chunking)** : `chunk_pages.chunk_page` — enforced
  par construction (copie verbatim de `page_num`, jamais de pdfplumber)
- **Tests amont** :
  - `tests/test_extract_pdf.py::test_extract_pages_expected_page_count_mismatch_raises`
  - `tests/test_extract_pdf.py::test_extract_doc_enforces_manifest_page_count`
  - `tests/test_extract_pdf.py::test_page_count_mismatch_is_catchable_as_corpus_error`
- **Tests aval (chunk-side)** :
  - `tests/test_chunk_pages.py::test_chunk_page_propagates_doc_id_and_page_num_verbatim`
  - `tests/test_chunk_pages.py::test_baseline_no_chunk_crosses_page_boundary_on_real_corpus`
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
- **Consommateur (chunking)** : `chunk_pages.chunk_all` (lit
  `pages.jsonl` via `_iter_pages_jsonl`, ne rouvre jamais un PDF)
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

## Chunking (`REQ-CHUNK-*`)

### `REQ-CHUNK-01` — Taille de chunk bornée

Chaque chunk émis par le chunker satisfait
`token_count(chunk.text, cl100k_base) <= MAX_TOKENS = 800`. Objectif :
garantir un budget de contexte prédictible en aval (embedding B2 puis
concaténation top-K en B4) et éviter qu'une régression du splitter
n'émette un chunk-monstre sans que rien ne le détecte.

**Enforcement** — deux niveaux redondants :

1. **Producteur** — `chunk_pages._split_recursive` cascade
   `["\n\n","\n",". "," ",""]`, chaque niveau descendant tant qu'un
   atome dépasse `MAX_TOKENS`. Le dernier niveau (`""`) déclenche
   `_hard_split_by_tokens`, binary-search sur la longueur en
   caractères garantissant `token_count(piece) <= MAX_TOKENS`.
2. **Merger** — `_merge_atoms_to_chunks` greedy jusqu'à `TARGET_TOKENS`,
   n'assemble jamais un chunk au-delà (sauf si un atome unique est
   déjà > TARGET, mais ≤ MAX par (1)).

**Statut** — *fully enforced*. Vérifié en unit sur inputs fabriqués
ET sur le corpus réel via
`tests/test_chunk_pages.py::test_baseline_every_chunk_under_max_tokens_on_real_corpus`.

**Paramètres pipeline associés (figés au `derived_artifacts.chunks_jsonl.producer_env`)** :

- `tokenizer`: `cl100k_base` (tiktoken 0.13.0). Choix documenté : proxy
  stable de complexité textuelle, indépendant du modèle d'embedding de
  B2. Swappable via `--tokenizer` si B4 révèle un biais retrieval.
- `target_tokens = 500`, `max_tokens = 800`. `TARGET` = sweet spot RAG
  (1 recommandation ANSSI = 1 chunk en majorité). `MAX = 1.6 × TARGET`
  laisse la cascade se résoudre sur un paragraphe unique long avant de
  tomber au hard-split.
- `overlap_tokens = 75` (~15%). Insurance contre le bug "recommandation
  coupée à cheval sur deux chunks". Consensus RAG (LangChain 20%,
  LlamaIndex 15-20%, Anthropic cookbook 10-15%).

- **Producteur** : `chunk_pages.chunk_page` (module) / `chunk_pages.chunk_all` (CLI)
- **Consommateur** : à venir (Brique 2 — embeddings)
- **Tests amont** :
  - `tests/test_chunk_pages.py::test_chunk_page_every_chunk_stays_under_max_tokens`
  - `tests/test_chunk_pages.py::test_hard_split_produces_pieces_all_under_max_tokens`
  - `tests/test_chunk_pages.py::test_baseline_every_chunk_under_max_tokens_on_real_corpus`
- **Exception** : `chunk_pages.ChunkTooLargeError` (hard-split n'a pas
  réussi à borner un piece — indiquerait une incohérence tokenizer,
  théoriquement inatteignable sur la cascade actuelle).

### `REQ-CHUNK-02` — Provenance immutable et strict-substring

Chaque chunk porte `(doc_id, page_num, chunk_idx, char_start, char_end)`
tel que `pages_jsonl_page.text[char_start:char_end] == chunk.text`
exactement (strict substring, byte-for-byte, sans normalisation ni
whitespace stripping caché). L'invariant permet à un consommateur du
VCD (Brique 7) de vérifier une citation en rechargeant
`corpus/pages.jsonl` — sans réouvrir de PDF, sans rejouer pdfplumber.

**Enforcement** — par construction dans `chunk_pages.chunk_page` :

1. Le splitter cascade (`_split_keeping_sep_left` + `_hard_split_by_tokens`)
   ne produit que des offsets en coordonnées absolues de la source ; la
   séparation est *glued-left* pour que la concaténation des atomes
   couvre `text[start:end]` sans gap.
2. Le merger (`_merge_atoms_to_chunks`) prend `chunk.char_start` du
   premier atome et `chunk.char_end` du dernier — la fenêtre est un
   intervalle contigu de l'input.
3. `chunk_page` construit chaque `Chunk` avec
   `text=page_text[char_start:char_end]` littéral.

L'invariant est vérifié par machine sur le corpus réel — voir tests.

**Statut** — *fully enforced*.

- **Producteur** : `chunk_pages.chunk_page`
- **Consommateur (VCD B7)** : à venir
- **Tests amont** :
  - `tests/test_chunk_pages.py::test_chunk_page_char_offsets_are_strict_substrings_of_source`
  - `tests/test_chunk_pages.py::test_baseline_strict_substring_invariant_on_real_corpus`
  - `tests/test_chunk_pages.py::test_split_recursive_produces_contiguous_atoms`
  - `tests/test_chunk_pages.py::test_hard_split_pieces_are_contiguous_and_cover_input`

### `REQ-CHUNK-03` — Baseline gelée du chunking (`chunks.jsonl`)

La sortie du chunker est persistée dans `corpus/chunks.jsonl`
(versionné) et son SHA256 est gelé au manifest sous
`derived_artifacts.chunks_jsonl.sha256`. Miroir de REQ-CORPUS-04 côté
chunking : toute régression silencieuse du splitter (bump tokenizer,
changement de constantes, refactor du merger qui déplace un caractère)
est détectée par machine sans dépendre d'un `git diff` humain.

**Motivation** — la Brique 2 (embeddings) consomme `chunks.jsonl` et
non le chunker en direct. Un chunking stable = des embeddings
reproductibles = un banc IVVQ auditable en aval sans dépendance à la
machine qui a fait tourner le chunking.

**Format** — JSONL, une ligne = un chunk, clés
`(doc_id, page_num, chunk_idx, char_start, char_end, text)` dans cet
ordre, `ensure_ascii=False`, ordre : documents selon l'ordre de
`pages.jsonl` (= ordre manifest), pages 1-indexées par doc,
`chunk_idx` 0-indexé par page. Fins de ligne LF figées par
`.gitattributes`.

**Statut** — *enforced*. Symétrique à REQ-CORPUS-04.

- **Producteur** : `chunk_pages.chunk_all`
- **Consommateur** : à venir (Brique 2 — embeddings)
- **Producer env pinné** : `tiktoken`, `tokenizer`, `target_tokens`,
  `max_tokens`, `overlap_tokens` déclarés sous
  `derived_artifacts.chunks_jsonl.producer_env`. Un swap déplace le
  SHA256 délibérément.
- **Tests amont (baseline)** :
  - `tests/test_chunk_pages.py::test_chunks_baseline_hash_matches_manifest` (**primaire**)
  - `tests/test_chunk_pages.py::test_chunks_baseline_uses_lf_line_endings_only`
  - `tests/test_chunk_pages.py::test_chunks_baseline_bytes_matches_manifest`
- **Test de déterminisme** :
  `tests/test_chunk_pages.py::test_chunk_all_second_run_is_bit_for_bit_identical`

## Statut

**Provisoire.** Registre définitif = Brique 5 (spec des cas de test du
banc de vérification). Les IDs actuels sont considérés stables : ils
seront **au minimum** conservés en Brique 5, éventuellement complétés
par les exigences propres au banc lui-même (`REQ-BENCH-*`), à la
génération de réponses (`REQ-GEN-*`), et au VCD (`REQ-VCD-*`).
