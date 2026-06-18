"""Loop Engineering — l'agent drive chaque étape, l'humain pose le goal et revoit le résultat.

Architecture (cf. diagramme Loop Engineering) :
  1. Human sets goal   →  loop.run(goal, success_criteria)
  2. Trigger fires     →  la boucle démarre automatiquement
  3. Agent acts        →  _act()       : le LLM génère une réponse
  4. Goal met?         →  _goal_met()  : un juge LLM évalue
     ├── NO  → feedback injecté, retour en 3
     └── YES → on sort de la boucle
  5. Human reviews output  →  LoopResult retourné à l'appelant
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


# ---------------------------------------------------------------------------
# Résultats
# ---------------------------------------------------------------------------

@dataclass
class GoalAssessment:
    """Verdict du juge sur une tentative (étape 4 du diagramme)."""
    met: bool
    score: int      # 0–10
    feedback: str   # pourquoi le goal n'est pas encore atteint
    tokens_used: int


@dataclass
class LoopResult:
    """Ce que l'humain reçoit pour review (étape 5 du diagramme)."""
    output: str
    goal_met: bool
    attempts: int
    history: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompts du juge
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM = """\
Tu es un juge autonome. Tu reçois un goal, une réponse candidate et des critères
de succès. Tu retournes UNIQUEMENT un JSON valide :
  {"met": bool, "score": int (0-10), "feedback": "str (1 phrase)"}
Aucun texte hors du JSON."""

_JUDGE_TEMPLATE = """\
Goal : {goal}

Réponse candidate :
{output}

Critères de succès :
{success_criteria}"""


# ---------------------------------------------------------------------------
# Loop Engineering
# ---------------------------------------------------------------------------

class GoalDrivenLoop:
    """Boucle agent-driven : l'agent agit jusqu'à ce que le goal soit atteint."""

    def __init__(
        self,
        client: OpenAI,
        model: str = "anthropic/claude-sonnet-4.6",
        judge_model: Optional[str] = None,
        max_attempts: int = 4,
        pass_threshold: int = 7,
    ):
        self.client = client
        self.model = model
        self.judge_model = judge_model or model
        self.max_attempts = max_attempts
        self.pass_threshold = pass_threshold

    # -- Étape 3 : Agent acts ------------------------------------------------

    def _act(self, messages: list[dict]) -> tuple[str, int]:
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=1024,
            messages=messages,
        )
        return resp.choices[0].message.content, resp.usage.total_tokens

    # -- Étape 4 : Goal met? -------------------------------------------------

    def _goal_met(self, goal: str, output: str, success_criteria: str) -> GoalAssessment:
        prompt = _JUDGE_TEMPLATE.format(
            goal=goal, output=output, success_criteria=success_criteria
        )
        resp = self.client.chat.completions.create(
            model=self.judge_model,
            max_tokens=256,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        raw = resp.choices[0].message.content.strip()
        try:
            data = json.loads(raw)
            score = int(data.get("score", 0))
            met = bool(data.get("met", False)) and score >= self.pass_threshold
            return GoalAssessment(
                met=met,
                score=score,
                feedback=str(data.get("feedback", "")),
                tokens_used=resp.usage.total_tokens,
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return GoalAssessment(
                met=False,
                score=0,
                feedback=f"Juge non parseable : {raw[:120]}",
                tokens_used=resp.usage.total_tokens,
            )

    # -- Trigger + boucle principale -----------------------------------------

    def run(self, goal: str, success_criteria: str) -> LoopResult:
        """
        Étapes 2-4 du diagramme — trigger + boucle autonome.
        L'appelant pose le goal (étape 1) et reçoit LoopResult (étape 5).
        """
        # Étape 2 : Trigger fires — on initialise le contexte conversationnel
        messages: list[dict] = [{"role": "user", "content": goal}]
        history: list[dict] = []
        assessment: Optional[GoalAssessment] = None
        output = ""

        for attempt in range(1, self.max_attempts + 1):

            # Étape 3 : Agent acts
            output, gen_tokens = self._act(messages)

            # Étape 4 : Goal met?
            assessment = self._goal_met(goal, output, success_criteria)

            history.append({
                "attempt": attempt,
                "output": output,
                "assessment": assessment,
                "gen_tokens": gen_tokens,
            })

            status = "GOAL MET" if assessment.met else "loop..."
            print(
                f"[{attempt}/{self.max_attempts}] score={assessment.score}/10 "
                f"{status} — {assessment.feedback}"
            )

            if assessment.met:
                break

            # NO → on injecte le feedback et on reboucle en étape 3
            if attempt < self.max_attempts:
                messages += [
                    {"role": "assistant", "content": output},
                    {
                        "role": "user",
                        "content": (
                            f"Pas encore. Feedback : {assessment.feedback} "
                            f"Reprends et améliore."
                        ),
                    },
                ]

        # Étape 5 : Human reviews output
        return LoopResult(
            output=output,
            goal_met=assessment.met if assessment else False,
            attempts=len(history),
            history=history,
        )


# ---------------------------------------------------------------------------
# Démo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )

    loop = GoalDrivenLoop(client, max_attempts=4, pass_threshold=7)

    # Étape 1 : Human sets goal
    result = loop.run(
        goal="Explique le principe de triple redondance dans les systèmes embarqués critiques.",
        success_criteria=(
            "La réponse doit : (1) définir le principe en 1-2 phrases, "
            "(2) citer au moins un exemple concret (aviation, spatial ou nucléaire), "
            "(3) rester sous 150 mots."
        ),
    )

    # Étape 5 : Human reviews output
    print("\n=== HUMAN REVIEWS OUTPUT ===")
    print(f"Goal atteint : {result.goal_met} ({result.attempts} tentative(s))")
    print(f"\n{result.output}")
