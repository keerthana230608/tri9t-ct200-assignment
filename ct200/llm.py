from __future__ import annotations

import json
import os

import httpx

from .schemas import TestCaseSet


def generate(prompt: str, count: int, allowed_keys: set[str]) -> tuple[dict, str, str]:
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("LLM_API_KEY is not configured")
    base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    provider = os.getenv("LLM_PROVIDER", "openai-compatible")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    error = None
    previous = ""
    for attempt in range(2):
        user_content = prompt if attempt == 0 else prompt + "\n\nYour previous JSON failed schema/source checks: " + previous + ". Return corrected JSON only."
        try:
            response = httpx.post(f"{base}/chat/completions", headers=headers, json={
                "model": model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "Produce conservative draft QA ideas. Use only the supplied source nodes. Never present a draft as validated medical-device testing."},
                    {"role": "user", "content": user_content},
                ],
            }, timeout=45)
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            data = json.loads(raw)
            parsed = TestCaseSet.model_validate(data)
            if len(parsed.test_cases) != count:
                raise ValueError(f"expected exactly {count} cases")
            if any(tc.source_node_key not in allowed_keys for tc in parsed.test_cases):
                raise ValueError("output referenced a node outside the pinned selection")
            return parsed.model_dump(), provider, model
        except Exception as exc:
            error = exc
            previous = str(exc)[:300]
    raise RuntimeError(f"LLM output/provider failed after one repair attempt: {error}") from error
