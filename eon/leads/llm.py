"""LLM calls for the scout, restricted to the workspace's allowed providers."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from eon.failover import RETRYABLE, classify, parse_chain
from eon.workspaces import Workspace, load_keys

ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
}
_KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}
_ENGINE_TO_PROVIDER = {"groq": "groq", "cloud": "gemini"}


def provider_plan(ws: Workspace) -> List[Tuple[str, str, str, str]]:
    """Ordered (provider, url, key, model) from the workspace chain and keys."""
    keys = load_keys(ws)
    plan = []
    for entry in parse_chain(ws.chain):
        prov = _ENGINE_TO_PROVIDER.get(entry.engine)
        if prov and prov in ws.providers and keys.get(_KEYS[prov]):
            plan.append((prov, ENDPOINTS[prov], keys[_KEYS[prov]], entry.model))
    return plan


def _http_post(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    r = httpx.post(url, headers=headers, json=payload, timeout=90.0)
    r.raise_for_status()
    return r.json()


def _retry_after(exc: BaseException, default: float = 8.0) -> float:
    """Seconds to wait before a retry: the provider's Retry-After header if present, else a short default."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        return min(max(float(headers.get("retry-after", default)), 1.0), 30.0)
    except (TypeError, ValueError):
        return default


def chat(ws: Workspace, system: str, user: str, *, max_tokens: int = 1800, temperature: float = 0.2,
         post: Callable[..., Dict[str, Any]] = _http_post, sleep: Callable[[float], None] = time.sleep) -> str:
    plan = provider_plan(ws)
    if not plan:
        raise RuntimeError(f"No usable provider key for workspace '{ws.name}'. Check keys.env.")
    last: Optional[BaseException] = None
    for prov, url, key, model in plan:
        for attempt in range(2):
            try:
                payload = {
                    "model": model, "temperature": temperature, "max_tokens": max_tokens,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
                if "gpt-oss" in model:
                    # Reasoning models spend output tokens on hidden thinking. Keep it short and
                    # leave room for the answer, or the visible reply comes back empty.
                    payload["max_tokens"] = max(max_tokens, 3000)
                    payload["reasoning_effort"] = "low"
                headers = {"Authorization": f"Bearer {key}"}
                try:
                    data = post(url, headers, payload)
                except Exception as exc:  # noqa: BLE001
                    code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
                    if "reasoning_effort" in payload and code == 400:
                        payload.pop("reasoning_effort")
                        data = post(url, headers, payload)
                    else:
                        raise
                text = data["choices"][0]["message"].get("content") or ""
                if not text.strip():
                    raise RuntimeError("model returned an empty reply (its token budget may have been used up)")
                return text
            except Exception as exc:  # noqa: BLE001
                last = exc
                kind = classify(exc)
                if kind == RETRYABLE and attempt == 0:
                    sleep(_retry_after(exc))
                    continue
                if kind == "fatal":
                    raise
                break
    raise RuntimeError(f"All providers failed; last error: {last.__class__.__name__}") from last


def extract_json(text: str) -> Dict[str, Any]:
    """Return the first balanced JSON object in ``text`` (models often add prose or fences)."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                esc = (ch == "\\") and not esc
                if ch == '"' and not esc:
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError(f"no JSON object found in model output (got: {text[:200]!r})")
