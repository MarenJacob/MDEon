"""Groq engine (OpenAI-compatible). Key is read from GROQ_API_KEY."""

from __future__ import annotations

from typing import List

from openjarvis.engine._openai_compat import _OpenAICompatibleEngine

# Speech and safety models cannot serve chat requests.
_NON_CHAT_MARKERS = ("whisper", "orpheus", "guard")


class GroqEngine(_OpenAICompatibleEngine):
    engine_id = "groq"
    is_cloud = True
    _default_host = "https://api.groq.com/openai"
    _api_prefix = "/v1"

    def health(self) -> bool:
        if not self._api_key:
            return False
        try:
            # Base class uses a 2 s probe, too tight on a phone connection.
            resp = self._client.get(f"{self._api_prefix}/models", timeout=8.0)
            return resp.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[str]:
        return [
            m
            for m in super().list_models()
            if not any(marker in m.lower() for marker in _NON_CHAT_MARKERS)
        ]
