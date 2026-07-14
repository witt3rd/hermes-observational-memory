"""Context engine implementation for Observational Memory."""

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.context_engine import ContextEngine
from agent.model_metadata import get_model_context_length, estimate_messages_tokens_rough
from .db import OMLedgerDB

logger = logging.getLogger(__name__)

HISTORICAL_TASK_HEADING = "## Historical Task Snapshot"
HISTORICAL_IN_PROGRESS_HEADING = "## Historical In-Progress State"
HISTORICAL_PENDING_ASKS_HEADING = "## Historical Pending User Asks"
HISTORICAL_REMAINING_WORK_HEADING = "## Historical Remaining Work"

COMPRESSED_SUMMARY_METADATA_KEY = "_compressed_summary"


class ObservationalMemoryEngine(ContextEngine):
    """Context engine that deterministically projects curated memories into the summary."""

    def __init__(self):
        self._name = "observational-memory"
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.threshold_tokens = 0
        self.context_length = 0
        self.compression_count = 0
        
        # V3 Defaults
        self.ratio = 0.68
        self.threshold_percent = 0.68
        self.protect_first_n = 3
        self.protect_last_n = 20
        
        self.db: Optional[OMLedgerDB] = None
        self.session_id: Optional[str] = None
        self.hermes_home: Optional[Path] = None

    @property
    def name(self) -> str:
        return self._name

    def on_session_start(self, session_id: str, **kwargs) -> None:
        """Initialize session and db path."""
        self.session_id = session_id
        
        # Resolve hermes_home
        home_arg = kwargs.get("hermes_home")
        if home_arg:
            self.hermes_home = Path(home_arg)
        else:
            self.hermes_home = Path.home() / ".hermes"
            
        # Ensure db directory exists
        db_dir = self.hermes_home
        if "profile" in kwargs:
            profile_name = kwargs["profile"]
            db_dir = self.hermes_home / "profiles" / profile_name
            
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "om_ledger.db"
        
        self.db = OMLedgerDB(db_path)
        self.db.ensure_session(session_id)
        
        # Load existing session stats if any
        stats = self.db.get_session_stats(session_id)
        if stats:
            self.compression_count = stats.get("compression_count", 0)

    def on_session_end(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        """Flush and clean up if needed."""
        self.session_id = None

    def on_session_reset(self) -> None:
        """Clear state for the session."""
        if self.db and self.session_id:
            self.db.reset_session(self.session_id)
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0

    def update_model(
        self,
        model: str,
        context_length: int,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
        api_mode: str = "",
    ) -> None:
        """Update active model context length."""
        self.context_length = context_length or 200000
        self.threshold_tokens = int(self.context_length * self.ratio)

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        """Track turn token usage."""
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)
        self.last_completion_tokens = usage.get("completion_tokens", 0)
        self.last_total_tokens = usage.get("total_tokens", 0)
        
        if self.db and self.session_id:
            self.db.update_session_tokens(
                self.session_id,
                self.last_prompt_tokens,
                self.last_completion_tokens,
                self.last_total_tokens
            )

    def should_compress(self, prompt_tokens: int = None) -> bool:
        """Trigger compaction when tokens exceed threshold."""
        tokens = prompt_tokens or self.last_prompt_tokens
        if not tokens or not self.threshold_tokens:
            return False
        return tokens >= self.threshold_tokens

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int = None,
        focus_topic: str = None,
    ) -> List[Dict[str, Any]]:
        """Project current active observations and reflections into the summary."""
        if not self.db or not self.session_id:
            logger.warning("No DB session initialized for Observational Memory compression")
            return messages

        # 1. Fetch active observations and reflections
        active_obs = self.db.get_active_observations(self.session_id)
        reflections = self.db.get_reflections(self.session_id)

        # 2. Build the projected summary text
        summary_content = self._render_memory_projection(active_obs, reflections)

        # 3. Assemble the head/tail boundaries
        # We preserve system prompt automatically (handled by run_agent / turn_context).
        # We protect protect_first_n non-system messages.
        first_n = []
        middle_idx = 0
        for idx, msg in enumerate(messages):
            if msg.get("role") == "system":
                continue
            if len(first_n) < self.protect_first_n:
                first_n.append(msg)
                middle_idx = idx + 1
            else:
                break

        # Protect the last N messages
        protect_last = max(1, self.protect_last_n)
        tail = messages[-protect_last:] if len(messages) > protect_last else messages[middle_idx:]

        # Create our deterministic summary message
        summary_msg = {
            "role": "assistant",
            "content": summary_content,
            COMPRESSED_SUMMARY_METADATA_KEY: True,
        }

        # Combine: System prompt is kept by framework. We return First N + Summary + Tail.
        compressed = []
        # Keep system prompt if it was explicitly inside messages
        for msg in messages[:middle_idx]:
            if msg.get("role") == "system":
                compressed.append(msg)
        
        compressed.extend(first_n)
        compressed.append(summary_msg)
        compressed.extend(tail)

        # Increment counts
        self.compression_count += 1
        self.db.increment_compression_count(self.session_id)

        logger.info(
            "OM Compaction complete: projected %d reflections and %d active observations. "
            "Total turns reduced from %d to %d.",
            len(reflections), len(active_obs), len(messages), len(compressed)
        )
        return compressed

    def _render_memory_projection(self, observations: List[Dict[str, Any]], reflections: List[Dict[str, Any]]) -> str:
        """Render deterministic projection of the ledger."""
        lines = [
            "[CONTEXT COMPACTION — OBSERVATIONAL MEMORY PROJECTED]",
            "This session has been compacted to fit your context window. Stale conversation history ",
            "is removed, but active memories are preserved here as direct factual references.",
            "",
            "## Active Reflections"
        ]
        
        if not reflections:
            lines.append("- No persistent reflections recorded yet.")
        for ref in reflections:
            lines.append(f"- [{ref['id']}] {ref['content']}")

        lines.extend([
            "",
            "## Active Observations Pool",
            "Factual, high-signal events captured from recent turns:"
        ])

        if not observations:
            lines.append("- No active observations in pool.")
        for obs in observations:
            lines.append(f"- [{obs['id']}] {obs['timestamp']} [{obs['relevance']}] {obs['content']}")

        lines.extend([
            "",
            "Respond ONLY to the latest user message that appears after this summary."
        ])
        return "\n".join(lines)
