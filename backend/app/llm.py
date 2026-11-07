"""LLM adapter — any OpenAI-compatible chat-completions endpoint.

Default: NVIDIA NIM (free tier) serving nvidia/nemotron-3-ultra. Swapping to
any other OpenAI-compatible provider is a config change (FINAL_PLAN.md §4:
"Provider SDK behind an adapter"). Uses plain requests; non-streaming;
JSON-first prompting with one bounded repair attempt.

All model calls are READ-ONLY in nature: the model never receives tools that
can write — it only sees the case-scoped context that the runner assembled.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Callable

import requests

from .config import settings


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    """Pull the outermost JSON object out of a model reply (which may include
    prose, markdown fences, or reasoning preamble)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        if start == -1:
            raise LLMError("no JSON object in model reply")
        # walk to the matching closing brace
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    text = text[start:i + 1]
                    break
        else:
            raise LLMError("unbalanced JSON in model reply")
    return json.loads(text)


def chat_json(system: str, user: str, *, max_tokens: int = 4096,
              temperature: float = 0.2, timeout: int = 120,
              repair_hint_fn: Callable[[str], str] | None = None) -> dict:
    """One JSON chat completion, with a single repair attempt on bad output."""
    api_key = os.getenv("NVIDIA_API_KEY", "")
    base = os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
    model = os.getenv("LLM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
    if not api_key:
        raise LLMError("NVIDIA_API_KEY not set")

    def call(messages: list[dict]) -> str:
        resp = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"model": model, "messages": messages,
                  "temperature": temperature, "top_p": 0.95,
                  "max_tokens": max_tokens, "stream": False},
            timeout=timeout,
        )
        if resp.status_code >= 400:
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        content = resp.json()["choices"][0]["message"].get("content") or ""
        if not content.strip():
            raise LLMError("empty model reply")
        return content

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    started = time.time()
    try:
        return _extract_json(call(messages))
    except (LLMError, json.JSONDecodeError) as first_err:
        if repair_hint_fn is None:
            raise
        messages.append({"role": "assistant", "content": "(invalid JSON omitted)"})
        messages.append({"role": "user",
                         "content": repair_hint_fn(str(first_err))})
        out = _extract_json(call(messages))  # one bounded repair attempt
        out["_repaired_after"] = str(first_err)[:200]
        out["_elapsed_s"] = round(time.time() - started, 1)
        return out
