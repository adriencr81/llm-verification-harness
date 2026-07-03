#!/usr/bin/env python3
"""Stop hook — juge externe pour le Loop Engineering.

Appelé par Claude Code à chaque Stop. Lit la dernière réponse depuis
le transcript de session, la soumet au juge LLM. Si le score est < 7,
exit(2) avec feedback → asyncRewake réveille Claude avec le feedback.

Stdin : {"session_id": "...", "stop_hook_active": true}

Best-effort par construction : une erreur de disponibilité du juge (API
down, réponse non parseable, transcript introuvable) ne doit jamais
bloquer la session Claude Code — elle est donc avalée en exit(0), mais
toujours loggée sur stderr pour rester visible (jamais un silent
fallback muet).
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import NoReturn

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

PASS_THRESHOLD = 7

JUDGE_SYSTEM = """\
Tu es un juge de réponses IA. On te donne le dernier échange (question + réponse).
Retourne UNIQUEMENT ce JSON :
{"score": int (0-10), "passed": bool, "feedback": "str (1 phrase)"}
passed=true si score >= 7 et la réponse répond clairement au goal."""

# Tolère une réponse juge entourée d'un fence markdown (```json ... ```
# ou ``` ... ```) — un juge qui respecte le contrat "JSON only" au sens
# large mais ajoute un fence ne doit pas planter le parsing en aval.
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def find_transcript(session_id: str) -> Path | None:
    """Cherche le fichier transcript JSONL pour ce session_id.

    Pas de fallback cross-session : sans session_id résolu, le hook doit
    se taire (exit 0) plutôt que juger la réponse d'une autre session.
    """
    base = Path.home() / ".claude" / "projects"
    if not base.exists() or not session_id:
        return None
    for f in base.rglob(f"{session_id}.jsonl"):
        return f
    return None


def extract_last_exchange(transcript_path: Path) -> tuple[str, str] | None:
    """Extrait la dernière paire (question humaine, réponse assistant)."""
    lines = transcript_path.read_text().strip().splitlines()
    question, response = "", ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = entry.get("role") or entry.get("type", "")
        content = entry.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        if not response and role == "assistant":
            response = str(content)[:2000]
        elif response and role in ("user", "human"):
            question = str(content)[:500]
            break
    if response:
        return question, response
    return None


def _parse_judge_json(raw: str) -> dict:
    """Parse la sortie du juge, en tolérant un fence markdown autour du JSON."""
    m = _FENCE_RE.match(raw.strip())
    body = m.group(1) if m else raw
    return json.loads(body)


def judge(question: str, response: str) -> dict:
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )
    prompt = f"Question : {question}\n\nRéponse : {response}"
    resp = client.chat.completions.create(
        model="anthropic/claude-haiku-4-5",
        max_tokens=128,
        messages=[
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    )
    raw = resp.choices[0].message.content.strip()
    return _parse_judge_json(raw)


def _pass_silently(reason: str) -> NoReturn:
    """Laisse passer la réponse, mais journalise pourquoi le juge n'a pas
    pu se prononcer — un fallback silencieux serait un finding disqualifiant
    au sens de l'axe 2 (IVVQ) du reviewer."""
    print(f"[verify_hook] best-effort : {reason}", file=sys.stderr)
    sys.exit(0)


def main():
    stdin_data = {}
    try:
        stdin_data = json.loads(sys.stdin.read())
    except Exception as e:
        _pass_silently(f"stdin illisible ({e})")

    session_id = stdin_data.get("session_id", "")
    transcript = find_transcript(session_id)

    if not transcript:
        _pass_silently(f"transcript introuvable pour session_id={session_id!r}")

    exchange = extract_last_exchange(transcript)
    if not exchange:
        _pass_silently(f"aucun échange exploitable dans {transcript}")

    question, response = exchange

    try:
        verdict = judge(question, response)
    except Exception as e:
        _pass_silently(f"juge indisponible ou réponse non parseable ({e})")

    score = int(verdict.get("score", 10))
    passed = verdict.get("passed", True)
    feedback = verdict.get("feedback", "")

    if not passed or score < PASS_THRESHOLD:
        # exit(2) + asyncRewake → Claude Code réveille l'agent avec ce message
        print(
            f"Vérification externe : score {score}/10. "
            f"Révise ta réponse — {feedback}",
            file=sys.stderr,
        )
        sys.exit(2)

    sys.exit(0)  # Réponse validée


if __name__ == "__main__":
    main()
