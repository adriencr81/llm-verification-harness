# BACKLOG_RAG.md — travail reporté au-delà de v1.0

**Statut** : gelé au tag `v1.0` (fin Brique 6).
**Contexte de la décision** : le plan Alfred (D1) acte que
le harnais RAG se ferme proprement à v1.0 pour libérer le focus sur
Alfred (paquet pip livrable J+45). Les briques 7-9 initialement prévues
dans la roadmap README, ainsi que quelques extensions naturelles de B6,
sont documentées ici plutôt que jetées — elles reviendront en v1.1+ ou
comme machine à contenu marketing d'Alfred.

Ce document est un contrat de gel, pas une TODO active : rien n'est
travaillé sur ce backlog tant qu'Alfred v0.1 n'a pas launch.

---

## Brique 7 — Verification Control Document (VCD) auto-généré

**Ce qui aurait été livré** : un module `vcd.py` qui prend
`list[CaseResult]` (produit par `bench_runner.run_cases`) et sort un
dossier Markdown signé/horodaté comprenant :

- Table des `REQ-*` couverts, avec pour chacun le case_id, le status
  (`PASS` / `TRACKED-FAIL` / `REGRESSION` / `UNEXPECTED-PASS` /
  `ERROR`), le modèle utilisé, la latence, les tokens.
- La `raw_response` du juge de faithfulness (REQ-FAITH-01) capturée
  verbatim pour audit — c'est le seul artefact où l'opacité du LLM
  juge devient inspectable.
- Un tableau de conformité "déclaré vs enforced" par `REQ-*` construit
  automatiquement à partir de `docs/REQUIREMENTS.md`.
- Signature GPG ou minisign du dossier + horodatage — un dossier
  d'audit qui vaut signature d'ingénieur, pas un log console.

**Nouveaux `REQ-VCD-*` prévus** : format du dossier, invariants
d'ancrage (chaque affirmation citée pointe un `case_id` réel),
non-régression bit-à-bit du template.

**Pourquoi c'est reporté** : le VCD est la valeur ajoutée long terme du
projet — mais Alfred adresse le même problème sur un substrat très
différent (traces d'agents plutôt que réponses RAG). Livrer Alfred v0.1
d'abord évite de fossiliser un format VCD qu'Alfred nous ferait
repenser trois mois plus tard.

---

## Brique 8 — Couverture OWASP LLM Top 10 étendue

**Ce qui aurait été livré** : cas de test formels par famille OWASP,
au-delà des trois familles déjà couvertes (LLM01 injection, LLM02 leak,
LLM09 overreliance) :

- **LLM03 — Training Data Poisoning** : hors périmètre de ce harnais
  (pas d'entraînement dans le pipeline), déprioritaire.
- **LLM04 — Model Denial of Service** : cas d'input trop long / trop
  répétitif provoquant coût explosif ou timeout.
- **LLM05 — Supply Chain** : SBOM + non-régression version du modèle
  d'embedding pinné (`REQ-EMBED-01` couvre déjà en partie).
- **LLM06 — Sensitive Information Disclosure** : partiellement couvert
  par REQ-LEAK-01, extension aux PII simulées.
- **LLM07 — Insecure Plugin Design** : n/a (pas de plugin dans ce
  RAG).
- **LLM08 — Excessive Agency** : n/a en v0.x (aucun outil appelable).
- **LLM10 — Model Theft** : n/a (modèle appelé via API).

**En parallèle : MITRE ATLAS**. Une matrice équivalente côté ATLAS
(TA0043 Reconnaissance, T1590 Gather Victim Network Information adapté
LLM, etc.). Le mapping OWASP↔ATLAS est un livrable de contenu
marketing à part entière — probablement le premier post technique post-
launch d'Alfred.

**Pourquoi c'est reporté** : la loi des rendements décroissants s'applique
aux familles OWASP à couvrir en amont d'utilisateurs réels. LLM01 +
LLM02 + LLM09 sont les trois vecteurs les plus concrètement questionnés
par un RSSI qui déploie un RAG aujourd'hui — les autres attendront des
demandes d'utilisateurs pour définir leur priorité, exactement le geste
V4 (validate) du plan Alfred.

---

## Brique 9 — Boucle de durcissement (detect → fix → re-verify)

**Ce qui aurait été livré** : pour chaque `REQ-*` en `TRACKED-FAIL`,
une contre-mesure implémentable + une re-vérification qui bascule le
status en `UNEXPECTED-PASS` puis en `PASS` après mise à jour de
`expected`.

Contre-mesures pressenties (à valider avec utilisateurs réels, pas ici) :
- **INJECT-01 hardening** : filtrage de contenu par regex sur les
  chunks retrieved avant injection dans le contexte, ou passage par
  un modèle sanitizer moins puissant.
- **LEAK-01 hardening** : détection de tokens à format canari
  (regex + entropie), refus de restitution en amont de l'appel LLM.
- **FAITH-01 hardening** : re-prompt du LLM avec "cite ta source pour
  chaque affirmation" quand le juge flag `grounded=False`.

**Pourquoi c'est reporté** : durcir sans utilisateurs réels revient à
optimiser à l'aveugle. La boucle de durcissement est plus utile après
avoir vu quelles contre-mesures les vrais RSSI/DSI demandent — même
raison que B8.

---

## Extensions de B6 délibérément non-livrées à v1.0

Ces items sont mentionnés dans le corps de B4/B5/B6 comme "Brique 6
extension" et n'ont pas été livrés dans le scope MVP retenu :

- **Variantes injection multi-tour / anglais / encodées**. Le baseline
  REQ-INJECT-01 reste sur la needle littérale française `PWNED-7Q2`.
  Une variante encodée (base64, ROT13, zero-width caractères) est un
  cas de test naturel — reportée. Réactivation quand un utilisateur
  réel rapporte un vecteur observé.
- **Variantes LLM02 encodées / partielles**. Analogue au point
  précédent : REQ-LEAK-01 reste sur le token littéral. Une leak
  partielle (`LEAK-CANARY-9X4B7` → `LEAK-...9X4B7`) ou encodée
  échapperait au check `no_forbidden_terms`. Reportée pour la même
  raison.
- **Granularité `expected` par-check dans `bench_runner`**. Aujourd'hui
  un case scalaire `expected: PASS|FAIL` couvre tous ses checks. Une
  extension naturelle (mentionnée dans REQ-BENCH-01) donnerait un
  `expected` par check individuel — utile pour un case qui mélange une
  précondition (`fake_doc_in_top_k`) et un check vérificateur. Reportée
  parce que le besoin ne s'est pas manifesté sur les 6 cas actuels.
- **Juge de faithfulness durci**. Voir REQ-FAITH-01 : le juge actuel
  est un seul modèle Haiku 4.5. Extensions naturelles : juge
  contrastif (deux modèles votent), juge multi-tour (le juge cite lui-
  même la partie du chunk qui supporte chaque affirmation), score de
  confiance calibré. Reportées.
- **Sentinelle de drift automatisée**. Aujourd'hui la drift se détecte
  quand quelqu'un relance `bench_runner.py` ; l'automatiser en CI
  planifiée (cron GitHub Actions hebdomadaire, publish delta report)
  serait un livrable propre — reporté parce que ça consomme un budget
  OpenRouter récurrent et parce qu'Alfred, précisément, va livrer ce
  genre de mécanisme pour les agents généraux.

---

## Ce qui NE reviendra PAS en v1.1

Décisions actives de *ne pas* rouvrir, même après Alfred :

- ❌ Ré-écriture du chunker en Python "pur maison" pour aller plus loin
  que la cascade `["\n\n","\n",". "," ",""]`. Un LangChain / LlamaIndex
  serait un enterrement pour deux fois moins de contrôle.
- ❌ Passage à un vector database (FAISS, Chroma, ...) tant que le
  corpus reste sous ~10k chunks. `matrix @ query` en float32 tient en
  <10 ms sur CPU, c'est un argument architectural, pas une dette.
- ❌ Bench multi-modèle en boucle. Le harnais est model-agnostique par
  design mais benchmarker N modèles est une pente marketing, pas une
  pente produit — sauf si un utilisateur payant en fait la demande
  explicite.

---

*Ce backlog est un artefact de gel, pas une roadmap. Toute reprise
d'un item ci-dessus exige d'abord une décision documentée (ADR ou
équivalent) dans le repo qui héberge le travail — Alfred ou ce
harnais, selon où l'item redevient prioritaire.*
