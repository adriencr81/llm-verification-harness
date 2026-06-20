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

# Cible — Poste visé

**Applied AI Engineer, CyberSecurity — Mistral AI**
Paris / London · Solutions · Full-time · Hybrid
Source : https://jobs.lever.co/mistral/98e6a2ea-7049-4c8d-88b7-c9d824eea6f1

## Résumé du rôle

Construire le service cyber client au-dessus des harnesses Mistral : composer des workflows agentiques red-team/blue-team, configurer des harnesses pour des scénarios réels, livrer directement de la valeur aux clients. Pont entre les harnesses fondationaux et les solutions agentiques déployées.

## Ce que Mistral attend

| Domaine | Signal explicite dans l'annonce |
|---|---|
| Agents & orchestration | Workflows agentiques red/blue-team, multi-agent, context engineering |
| Evals & benchmarking | Expérience en évals et benchmarking de systèmes agentiques |
| RAG | RAG systems (mentionné dans "About you") |
| LLM orchestration | LLM orchestration, context engineering |
| Harness end-to-end | "Agentic harnesses or multi-agent systems end-to-end" |
| Cyber/pentest | Bonus fort — connaissance vulnérabilités, red-team, pentest |
| Livraison rapide | "Ship fast, bias toward action and delivery" |
| Systèmes production | "Production AI systems in regulated or high-stakes environments" |

## Ce que ce projet doit prouver

1. **Harness d'évaluation LLM complet** — pipeline RAG + test cases formalisés + génération de rapport (VCD) → démontre la capacité à construire un harness end-to-end
2. **Couverture sécurité réelle** — OWASP LLM Top 10 (injection, leak, faithfulness) + MITRE ATLAS → signal cyber sans avoir besoin du titre "pentester"
3. **Evals structurés** — acceptance criteria par requirement, verdict formel, traceability → exactement ce que l'annonce appelle "strong background in evals and benchmarking"
4. **LLM-as-judge** — agent qui évalue un autre agent → démontre la maîtrise de l'orchestration multi-agent
5. **Vitesse de livraison** — brique par brique, commits réguliers, README à jour → signal "ship fast"
6. **Boucle detect → fix → re-verify** (Brique 9) → analogie directe avec les workflows red-team/blue-team du rôle
7. **Duel agentique Red/Blue** (Brique 10) — deux agents à outils s'affrontent sur le pipeline RAG, orchestrés par le harness → démontre multi-agent end-to-end + context engineering

## Architecture Brique 10 — Red/Blue Agentic Duel

### Principe

Deux agents à outils (function calling) opèrent sur la même base documentaire RAG.
Le harness orchestre la séquence et le Judge évalue les trajectoires (pas juste les outputs).

### Red Agent (attaquant)

**Goal** : faire répondre au RAG quelque chose de faux ou malveillant (OWASP LLM01).

Outils :
- `list_documents()` — inventaire de la base
- `inject_document(content)` — injecte un doc empoisonné
- `test_query(question)` — lance une vraie requête RAG et lit la réponse

Se termine quand : il estime que son injection a réussi (la réponse RAG contient sa payload).

### Blue Agent (défenseur)

**Goal** : détecter l'injection avant ou après que Red ait réussi.

Outils :
- `list_documents()` — inventaire de la base
- `read_document(doc_id)` — lit un doc en entier
- `flag_document(doc_id, reason)` — marque un doc comme suspect
- `faithfulness_check(query, response, sources)` — score de fidélité aux sources légitimes

Se termine quand : il a inspecté la base et produit son rapport de détection.

### Judge Agent (arbitre)

Reçoit la trajectoire complète des deux agents (chaque tool call + résultat) et répond :
1. Red a-t-il réussi à polluer la réponse RAG ?
2. Blue a-t-il détecté le bon document ? En combien d'étapes ?
3. Score 0-10 pour chaque agent + feedback.

### Séquence orchestrateur

```
[1] Red Agent s'exécute   →  injection dans la base documentaire
[2] RAG query lancée      →  réponse capturée (avant intervention Blue)
[3] Blue Agent s'exécute  →  détection (ou non) du doc injecté
[4] Judge évalue          →  verdict structuré sur les deux trajectoires
[5] VCD généré            →  rapport : Red score, Blue score, statut OWASP LLM01
```

### Implémentation

- Base documentaire : liste de dicts en mémoire (10-15 docs fictifs)
- Retrieval : cosine similarity sur embeddings (réutilise Brique 2)
- Function calling : paramètre `tools=` du SDK OpenAI (déjà utilisé)
- Orchestrateur : étend `GoalDrivenLoop` de `verification_loop.py`
- Chaque agent a un system prompt spécialisé (context engineering explicite)

## Mantra de développement

Chaque brique livrée doit pouvoir répondre à la question :
> "En quoi ce composant démontre-t-il une compétence explicitement citée dans l'annonce Mistral ?"

---

# Profil candidat — Adrien Deleuil

Ingénieur IVVQ cyber spatial. Toulouse.

| Expérience | Signal |
|---|---|
| Airbus Defence & Space — IVVQ cyber Onesat (2024–) | Environnement régulé haute criticité |
| Bertrandt — IVV Egnos V3 | Systèmes critiques spatial |
| Isika — Concepteur-développeur fullstack DevOps | Stack technique |
| 7 ans gérant SARL Cabot | Autonomie, client, gestion, croissance |
| Darty / IXINA / Next Idea | Communication, relation client |
| ISTQB, Agile Tester, LPIC-1, Google Cybersecurity | Certifications |

**Atout différenciateur** : le parcours pré-tech (7 ans dirigeant + socle commercial/rédactionnel) n'est pas du vide — c'est la capacité à traduire un risque technique en décision pour un non-technicien, et à opérer en autonomie client. Rare chez un ingénieur IVVQ.

**Gap principal** : pas encore d'AI engineering commercial (agents, RAG en production). Ce projet est la preuve d'exécution qui compense.

---

# Stratégie de candidature

## Principe

Ne pas attendre d'être "prêt". Lancer deux axes en parallèle dès que les Briques 1-3 sont livrées.

## Axe 1 — Candidature Mistral directe

Postuler avec le projet comme preuve d'exécution. Présenter chaque brique comme un choix d'ingénierie motivé par les signaux de l'annonce, pas comme un side project.

Angle de pitch : *"ingénieur vérification de systèmes critiques qui transpose sa méthode aux systèmes IA"*.

## Axe 2 — Étape intermédiaire en parallèle

Cibler 2-3 boîtes où le profil actuel entre directement ET où on touche de l'IA concrète :
- Éditeurs cyber intégrant de l'IA (détection d'anomalies, scoring de menaces)
- Défense/aérospatial buildant de l'IA (Thales, CS Group, Sopra Steria Defence)
- Cabinets de conseil AI sur missions cyber

Objectif : "AI engineer en production" sur le CV en 12-18 mois, puis candidature Mistral sans ambiguïté.

## Déclencheur

Lancer les candidatures dès que **Brique 3 (RAG complet)** est livrée — c'est le seuil minimal pour que le projet soit défendable en entretien technique.
