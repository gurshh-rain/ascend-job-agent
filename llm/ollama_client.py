"""
Thin wrapper around a local Ollama /api/chat endpoint.

Import and use:
    from llm.ollama_client import chat
    response = chat("Classify this listing", system="You are a helpful classifier...")

Run directly to test connectivity:
    python -m llm.ollama_client
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

# Allow running this file directly.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings


def chat(prompt: str, system: str | None = None, options: dict | None = None) -> str:
    """
    Send a single chat prompt to the configured Ollama model and return the
    model's response text.

    Uses Ollama's /api/chat with format="json" and stream=False.
    Raises a clear RuntimeError if Ollama is not reachable.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": messages,
        "format": "json",
        "stream": False,
        "options": options or {"temperature": 0.0, "seed": 42},
    }

    url = f"{settings.OLLAMA_HOST.rstrip('/')}/api/chat"
    try:
        response = requests.post(url, json=payload, timeout=120)
        response.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            f"Could not connect to Ollama at {settings.OLLAMA_HOST}. "
            f"Is Ollama running and is model '{settings.OLLAMA_MODEL}' pulled?"
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise RuntimeError(
            f"Ollama at {settings.OLLAMA_HOST} timed out after 120s."
        ) from exc

    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Ollama returned non-JSON: {response.text[:200]}"
        ) from exc

    return data.get("message", {}).get("content", "")


if __name__ == "__main__":
    print(f"Testing Ollama at {settings.OLLAMA_HOST} with model {settings.OLLAMA_MODEL}")
    try:
        reply = chat(
            prompt='Return a JSON object with a single key "status" set to "ok".',
            system="You are a terse JSON-only assistant.",
        )
        print("Ollama replied:")
        print(reply)
    except RuntimeError as exc:
        print(f"Error: {exc}")
