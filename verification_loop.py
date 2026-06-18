"""Brique 0.5 — boucle de vérification de réponse LLM (LLM-as-judge).

Génère une réponse, la soumet à un juge LLM, et recommence si les critères
ne sont pas satisfaits. Fournit une trace d'audit complète (anticipe le VCD
de Brique 7 et la hardening loop de Brique 9).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


@dataclass
class VerificationResult:
    passed: bool
    score: int      # 0–10
    reason: str
    tokens_used: int


@dataclass
class LoopResult:
    response: str
    final_verdict: VerificationResult
    attempts: int
    history: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.final_verdict.passed


_VERIFIER_SYSTEM = """\
Tu es un juge d'évaluation de réponses LLM. Tu reçois une question, une réponse
candidate et des critères d'acceptation. Tu retournes UNIQUEMENT un objet JSON
valide avec les champs :
  "passed" (bool), "score" (int 0-10), "reason" (str, 1 phrase max).
Aucun texte hors du JSON."""

_VERIFIER_TEMPLATE = """\
Question : {question}

Réponse candidate :
{response}

Critères d'acceptation :
{criteria}"""


class ResponseVerificationLoop:
    """Boucle generate → verify → retry avec juge LLM."""

    def __init__(
        self,
        client: OpenAI,
        model: str = "anthropic/claude-sonnet-4.6",
        verifier_model: Optional[str] = None,
        max_retries: int = 3,
        pass_threshold: int = 7,
    ):
        self.client = client
        self.model = model
        self.verifier_model = verifier_model or model
        self.max_retries = max_retries
        self.pass_threshold = pass_threshold

    def _generate(self, messages: list[dict]) -> tuple[str, int]:
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=1024,
            messages=messages,
        )
        return resp.choices[0].message.content, resp.usage.total_tokens

    def _verify(self, question: str, response: str, criteria: str) -> VerificationResult:
        prompt = _VERIFIER_TEMPLATE.format(
            question=question, response=response, criteria=criteria
        )
        resp = self.client.chat.completions.create(
            model=self.verifier_model,
            max_tokens=256,
            messages=[
                {"role": "system", "content": _VERIFIER_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
        raw = resp.choices[0].message.content.strip()
        try:
            data = json.loads(raw)
            score = int(data.get("score", 0))
            passed = bool(data.get("passed", False)) and score >= self.pass_threshold
            return VerificationResult(
                passed=passed,
                score=score,
                reason=str(data.get("reason", "")),
                tokens_used=resp.usage.total_tokens,
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return VerificationResult(
                passed=False,
                score=0,
                reason=f"Verifier output non parseable : {raw[:120]}",
                tokens_used=resp.usage.total_tokens,
            )

    def run(self, question: str, criteria: str) -> LoopResult:
        """Boucle principale : jusqu'à max_retries tentatives generate → verify."""
        messages: list[dict] = [{"role": "user", "content": question}]
        history: list[dict] = []
        verdict: Optional[VerificationResult] = None
        response = ""

        for attempt in range(1, self.max_retries + 1):
            response, gen_tokens = self._generate(messages)
            verdict = self._verify(question, response, criteria)

            history.append({
                "attempt": attempt,
                "response": response,
                "verdict": verdict,
                "gen_tokens": gen_tokens,
            })

            print(
                f"[Tentative {attempt}/{self.max_retries}] "
                f"score={verdict.score}/10  passed={verdict.passed}"
                f"  — {verdict.reason}"
            )

            if verdict.passed:
                break

            # Injecte le retour du juge pour guider la tentative suivante
            if attempt < self.max_retries:
                messages += [
                    {"role": "assistant", "content": response},
                    {
                        "role": "user",
                        "content": (
                            f"Ta réponse précédente n'est pas satisfaisante : "
                            f"{verdict.reason}. Reprends et améliore-la."
                        ),
                    },
                ]

        return LoopResult(
            response=response,
            final_verdict=verdict,
            attempts=len(history),
            history=history,
        )


if __name__ == "__main__":
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )

    loop = ResponseVerificationLoop(client, max_retries=3, pass_threshold=7)

    result = loop.run(
        question="Explique le principe de triple redondance dans les systèmes embarqués critiques.",
        criteria=(
            "La réponse doit : (1) définir le principe en 1-2 phrases, "
            "(2) mentionner au moins un exemple concret d'application, "
            "(3) rester sous 150 mots."
        ),
    )

    print("\n=== RÉSULTAT FINAL ===")
    print(f"Statut : {'ACCEPTÉ' if result.passed else 'REFUSÉ'} "
          f"après {result.attempts} tentative(s)")
    print(f"Score final : {result.final_verdict.score}/10")
    print(f"\n{result.response}")
