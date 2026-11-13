"""EON inference engines."""

from __future__ import annotations

from openjarvis.core.registry import EngineRegistry

from eon.engines.groq import GroqEngine


def register_engines() -> None:
    """Register EON engines with OpenJarvis (idempotent)."""
    if not EngineRegistry.contains("groq"):
        EngineRegistry.register_value("groq", GroqEngine)


__all__ = ["GroqEngine", "register_engines"]
