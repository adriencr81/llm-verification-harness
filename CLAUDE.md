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

## Mantra de développement

Chaque brique livrée doit pouvoir répondre à la question :
> "En quoi ce composant démontre-t-il une compétence explicitement citée dans l'annonce Mistral ?"
