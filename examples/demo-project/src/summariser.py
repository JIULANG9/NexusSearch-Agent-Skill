"""Blocking single-shot LLM call. The example research target asks whether an
agent framework would be a better home for this code."""

import json
import urllib.request

API_URL = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-4o-mini"
TIMEOUT_SECONDS = 30


def summarise(body: str) -> str:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": f"Summarise for an engineer:\n{body}"}],
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        parsed = json.load(response)
    return parsed["choices"][0]["message"]["content"]
