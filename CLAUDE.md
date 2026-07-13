# Loop Engineering — Verification Protocol

## Principe

Avant chaque réponse finale, appliquer ce cycle :

```
1. Cerner le goal de l'utilisateur (pas juste la question, l'intention)
2. Produire un draft interne
3. Évaluer : est-ce que ce draft atteint le goal ?
   - Répond-il EXACTEMENT à ce qui a été demandé ?
   - Manque-t-il une information critique ?
   - Y a-t-il une ambiguïté non résolue ?
4. Si NON → identifier le gap, réviser, retourner en 3
5. Si OUI → livrer
```

## Règles

- Ne jamais livrer un premier draft sans passer par l'étape 3.
- Si le goal est ambigu, clarifier avant d'agir (1-2 questions max).
- L'humain ne voit que le résultat final — le loop reste interne.
- Limiter à 3 itérations max pour ne pas bloquer sur des cas irréductibles.

## Critères de validation minimaux

Une réponse passe si elle répond OUI à toutes ces questions :
- Est-ce que ça répond directement au goal ?
- Est-ce factuel et vérifiable (ou clairement marqué comme opinion) ?
- Est-ce que c'est actionnable pour l'utilisateur ?
- Est-ce concis (pas de rembourrage) ?

---

# Senior IVVQ Review — Trigger Discipline

Un deuxième niveau de vérification existe : le subagent **senior-ivvq-reviewer** (défini dans `.claude/agents/senior-ivvq-reviewer.md`). Il porte une deuxième paire d'yeux "senior IVVQ" sur les artefacts du projet, avec un verdict `SHIP | REWORK | BLOCK` sur 3 axes (Code / IVVQ / Alignement narratif+employeurs).

## Quand l'invoquer AUTOMATIQUEMENT (via l'outil Agent)

Déclencher **avant de livrer à Adrien** dès qu'un des cas suivants est réuni :

1. **Code non-trivial** : ≥20 lignes de Python destinées à être conservées (pas les snippets pédagogiques jetables).
2. **Artefact IVVQ** : tout nouveau fichier ou modification de `corpus/manifest.yaml`, spec de cas de test, YAML runner (Brique 5), section de VCD (Brique 7).
3. **Décision d'architecture** : tout choix qui influence >1 brique (choix de librairie, format de stockage des chunks, format des cas de test, stratégie de versionnement).
4. **Post LinkedIn** : tout brouillon de post, avant publication.
5. **Commit de clôture de brique** : avant le commit `Brique N — ...` qui marque la fin d'une brique.

## Quand l'invoquer À LA DEMANDE

Sur demande explicite d'Adrien ("senior review", "passe ça en senior review", "review sénior sur X").

## Ce qu'on NE PASSE PAS au reviewer

- Réponses purement conversationnelles / questions de cadrage.
- Explications pédagogiques sans artefact produit.
- Snippets < 20 lignes clairement jetables.
- Modifications triviales (typo, renommage 1-pour-1, ajustement de commentaire).

## Comment on l'invoque

Via l'outil `Agent` avec `subagent_type=senior-ivvq-reviewer`. Le prompt doit contenir :
- L'artefact à réviser (chemin(s) + résumé de l'intention).
- Le contexte de la brique en cours.
- La question précise si elle est plus étroite qu'une revue complète.

Le verdict est restitué à Adrien tel quel — pas de paraphrase, pas de filtrage. S'il dit `BLOCK`, on ne ship pas ; on remonte les findings et on itère.

---

# Behavioral Guidelines

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
