"""Brique 0 — premier contact avec un LLM via OpenRouter.

OpenRouter expose une API compatible OpenAI qui route vers Claude, GPT,
Mistral, etc. selon le `model` choisi. Le pattern reste identique : on
envoie un prompt, on reçoit une réponse. Lancer deux fois pour observer
le non-déterminisme.
"""

import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Le base_url est ce qui transforme un client "OpenAI" en client OpenRouter.
# Le SDK OpenAI ne fait que parler HTTP — pointe-le ailleurs, il appelle ailleurs.
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

PROMPT = "Définis le concept d'IVVQ en deux phrases courtes."

response = client.chat.completions.create(
    model="anthropic/claude-sonnet-4.6",  # ← à vérifier dans ton interface OR
    max_tokens=1024,
    messages=[
        {"role": "user", "content": PROMPT}
    ],
)

# Format de réponse OpenAI : .choices[0].message.content
print(response.choices[0].message.content)

print(f"\n--- Tokens : {response.usage.prompt_tokens} in, "
      f"{response.usage.completion_tokens} out ---")