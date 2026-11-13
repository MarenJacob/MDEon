"""Provider failover for EON (env EON_FAILOVER_CHAIN="engine:model,engine:model")."""

from __future__ import annotations

import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Deque, Dict, List, Optional, Sequence

from openjarvis.engine._base import EngineConnectionError
from openjarvis.engine.multi import MultiEngine

logger = logging.getLogger("eon.failover")

RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
# 401/403: key expired or revoked. 404: provider retired the model.
FAILOVER_STATUS = frozenset({401, 403, 404})
_HTTP_RE = re.compile(r"HTTP (\d{3})")
_TRANSIENT_MARKERS = (
    "unavailable",
    "resource_exhausted",
    "high demand",
    "rate limit",
    "overloaded",
    "timed out",
    "not reachable",
)

RETRYABLE = "retryable"
UNAVAILABLE = "unavailable"
FATAL = "fatal"


class FailoverExhausted(EngineConnectionError):
    """Every provider in the chain failed."""


@dataclass(frozen=True)
class ChainEntry:
    engine: str
    model: str


def parse_chain(spec: Optional[str]) -> List[ChainEntry]:
    entries: List[ChainEntry] = []
    for raw in (spec or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        engine, sep, model = raw.partition(":")
        if not sep or not engine.strip() or not model.strip():
            logger.warning("Ignoring malformed failover entry %r", raw)
            continue
        entries.append(ChainEntry(engine.strip(), model.strip()))
    return entries


def _status_of(exc: BaseException) -> Optional[int]:
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int):
        return value
    match = _HTTP_RE.search(str(exc))
    return int(match.group(1)) if match else None


def classify(exc: BaseException) -> str:
    """Return RETRYABLE, UNAVAILABLE or FATAL for a provider error."""
    seen = 0
    current: Optional[BaseException] = exc
    while current is not None and seen < 6:
        status = _status_of(current)
        if status in RETRYABLE_STATUS:
            return RETRYABLE
        if status in FAILOVER_STATUS:
            return UNAVAILABLE
        text = str(current).lower()
        if any(marker in text for marker in _TRANSIENT_MARKERS):
            return RETRYABLE
        if current.__class__.__name__ in ("TimeoutException", "ConnectError"):
            return RETRYABLE
        current = current.__cause__ or current.__context__
        seen += 1
    return FATAL


class EonMultiEngine(MultiEngine):
    """MultiEngine with an ordered failover chain and an audit trail."""

    def __init__(
        self,
        *args: Any,
        chain: Optional[Sequence[ChainEntry]] = None,
        retries_same: int = 1,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._chain: List[ChainEntry] = (
            list(chain)
            if chain is not None
            else parse_chain(os.environ.get("EON_FAILOVER_CHAIN"))
        )
        self._retries_same = retries_same
        self._backoff = backoff
        self._sleep = sleep
        self.events: Deque[Dict[str, Any]] = deque(maxlen=200)

    def _engine_by_key(self, key: str) -> Any:
        for engine_key, engine in self._engines:
            if engine_key == key:
                return engine
        return None

    def _attempts(self, model: str) -> List[tuple]:
        # Config errors (unknown model) must surface, never be masked.
        primary = self._engine_for(model)
        primary_key = self._model_key_map.get(model) or "primary"
        attempts = [(primary_key, primary, model)]
        seen = {(primary_key, model)}
        for entry in self._chain:
            engine = self._engine_by_key(entry.engine)
            if engine is None or (entry.engine, entry.model) in seen:
                continue
            seen.add((entry.engine, entry.model))
            attempts.append((entry.engine, engine, entry.model))
        return attempts

    def _record(self, src: str, dst: Optional[str], kind: str, exc: BaseException) -> None:
        event = {
            "ts": time.time(),
            "from": src,
            "to": dst,
            "reason": kind,
            "error": exc.__class__.__name__,
            "status": _status_of(exc),
        }
        self.events.append(event)
        level = logging.WARNING if kind == UNAVAILABLE else logging.INFO
        logger.log(level, "EON failover: %s -> %s (%s, status=%s)", src, dst, kind, event["status"])

    def _exhausted(self, attempts: List[tuple], last: BaseException) -> FailoverExhausted:
        names = ", ".join(f"{key}:{mdl}" for key, _e, mdl in attempts)
        return FailoverExhausted(f"All providers failed ({names}); last error: {last.__class__.__name__}")

    def generate(self, messages, *, model, temperature=0.7, max_tokens=1024, **kwargs):
        attempts = self._attempts(model)
        last: Optional[BaseException] = None
        for idx, (key, engine, mdl) in enumerate(attempts):
            tries = 0
            while True:
                try:
                    result = engine.generate(
                        messages,
                        model=mdl,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **kwargs,
                    )
                    if idx > 0 and isinstance(result, dict):
                        result["eon_failover"] = {"from": attempts[0][0], "to": key, "model": mdl}
                    return result
                except Exception as exc:  # noqa: BLE001
                    kind = classify(exc)
                    if kind == FATAL:
                        raise
                    if kind == RETRYABLE and tries < self._retries_same:
                        self._sleep(self._backoff * (2**tries))
                        tries += 1
                        continue
                    last = exc
                    nxt = attempts[idx + 1][0] if idx + 1 < len(attempts) else None
                    self._record(key, nxt, kind, exc)
                    break
        assert last is not None
        raise self._exhausted(attempts, last) from last

    async def stream(self, messages, *, model, temperature=0.7, max_tokens=1024, **kwargs) -> AsyncIterator[str]:
        attempts = self._attempts(model)
        last: Optional[BaseException] = None
        for idx, (key, engine, mdl) in enumerate(attempts):
            started = False
            try:
                async for token in engine.stream(
                    messages, model=mdl, temperature=temperature, max_tokens=max_tokens, **kwargs
                ):
                    started = True
                    yield token
                return
            except Exception as exc:  # noqa: BLE001
                kind = classify(exc)
                # After tokens reached the caller, switching would duplicate text.
                if started or kind == FATAL:
                    raise
                last = exc
                nxt = attempts[idx + 1][0] if idx + 1 < len(attempts) else None
                self._record(key, nxt, kind, exc)
        assert last is not None
        raise self._exhausted(attempts, last) from last

    async def stream_full(self, messages, *, model, **kwargs):
        attempts = self._attempts(model)
        last: Optional[BaseException] = None
        for idx, (key, engine, mdl) in enumerate(attempts):
            started = False
            try:
                async for chunk in engine.stream_full(messages, model=mdl, **kwargs):
                    started = True
                    yield chunk
                return
            except Exception as exc:  # noqa: BLE001
                kind = classify(exc)
                if started or kind == FATAL:
                    raise
                last = exc
                nxt = attempts[idx + 1][0] if idx + 1 < len(attempts) else None
                self._record(key, nxt, kind, exc)
        assert last is not None
        raise self._exhausted(attempts, last) from last
