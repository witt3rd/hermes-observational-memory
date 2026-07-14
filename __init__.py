"""Hermes Observational Memory Plugin.

Keeps long agent sessions coherent across compactions and days of work by
maintaining a curated observations pool and reflections ledger in the background.
"""

from pathlib import Path
from typing import Any, Dict, List

from .engine import ObservationalMemoryEngine
from .hook import system_prompt_handler


def register(ctx):
    """Register the context engine and hooks with the Hermes plugin manager."""
    # Retrieve model-metadata context length or default to standard
    engine = ObservationalMemoryEngine()
    ctx.register_context_engine(engine)
    
    # Register the system_prompt hook to drive background observation/reflection
    ctx.register_hook(
        "system_prompt",
        system_prompt_handler,
    )
