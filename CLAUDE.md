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
