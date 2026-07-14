"""Background observer, reflector, and dropper hooks for Observational Memory."""

import datetime
import json
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

from agent.auxiliary_client import call_llm
from agent.model_metadata import estimate_messages_tokens_rough
from .db import OMLedgerDB

logger = logging.getLogger(__name__)


def system_prompt_handler(agent: Any, session_id: str, conversation_history: List[Dict[str, Any]], **kwargs) -> Optional[Dict[str, Any]]:
    """Drives incremental background observation and reflection based on session token progress."""
    engine = getattr(agent, "_context_engine", None)
    if not engine or engine.name != "observational-memory":
        # Only active when chosen in config.yaml
        return None

    db = getattr(engine, "db", None)
    if not db:
        logger.debug("OM database not initialized on active context engine")
        return None

    # Ensure current session is tracked
    db.ensure_session(session_id)

    # Drive background workers
    try:
        _drive_background_memory(db, session_id, conversation_history)
    except Exception as e:
        logger.warning("Failed to drive background Observational Memory: %s", e, exc_info=True)

    # We do not inject duplicate summary content into the system prompt while messages are uncompressed
    return None


def _drive_background_memory(db: OMLedgerDB, session_id: str, messages: List[Dict[str, Any]]):
    """Run incremental background passes (Observer, Reflector, Dropper)."""
    # 1. Check for unobserved messages
    watermarks = db.get_watermarks(session_id)
    last_observed_id = watermarks.get("last_observed_id")
    raw_obs_clock = watermarks.get("raw_tokens_since_observation", 0)
    raw_ref_clock = watermarks.get("raw_tokens_since_reflection", 0)

    # Find the slice of messages after the last observed watermark
    unobserved_slice = []
    found_watermark = last_observed_id is None
    
    for msg in messages:
        # Generate stable mock IDs for messages if they don't exist
        if "id" not in msg:
            msg["id"] = str(uuid.uuid4())[:8]
            
        if not found_watermark:
            if msg.get("id") == last_observed_id:
                found_watermark = True
            continue
            
        # Ignore system messages and metadata-only summaries
        if msg.get("role") == "system" or msg.get("_compressed_summary"):
            continue
            
        unobserved_slice.append(msg)

    if not unobserved_slice:
        return

    # Calculate token size of the unobserved slice
    new_tokens = estimate_messages_tokens_rough(unobserved_slice)
    raw_obs_clock += new_tokens
    raw_ref_clock += new_tokens

    observe_threshold = 10000  # V3 default observeAfterTokens
    reflect_threshold = 20000  # V3 default reflectAfterTokens

    logger.debug(
        "OM Progress: +%d tokens. Observer clock: %d/%d, Reflector clock: %d/%d",
        new_tokens, raw_obs_clock, observe_threshold, raw_ref_clock, reflect_threshold
    )

    # Update clocks in DB
    db.update_clocks(session_id, raw_obs_clock, raw_ref_clock)

    # Run Observer if due
    if raw_obs_clock >= observe_threshold:
        logger.info("OM: Running Observer pass for session %s...", session_id)
        _run_observer(db, session_id, unobserved_slice)
        # Reset observation clock but keep remaining reflection clock progress
        watermarks = db.get_watermarks(session_id)
        raw_obs_clock = watermarks.get("raw_tokens_since_observation", 0)

    # Run Reflector & Dropper if due
    if raw_ref_clock >= reflect_threshold:
        logger.info("OM: Running Reflector pass for session %s...", session_id)
        _run_reflector(db, session_id)
        _run_dropper(db, session_id)


def _run_observer(db: OMLedgerDB, session_id: str, messages: List[Dict[str, Any]]):
    """Call auxiliary LLM to extract new factual observations."""
    # Format the transcript slice for the observer
    transcript = []
    for idx, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        msg_id = msg.get("id", str(idx))
        transcript.append(f"[{msg_id}] {role}: {content}")
    
    formatted_transcript = "\n".join(transcript)

    prompt = f"""You are the Observer in an Observational Memory system. Your job is to extract new, atomic, factual observations from the following conversation transcript slice.

Factual observations are specific events, decisions, requirements, code changes, paths, commands, or errors that occurred during these turns.
Ignore conversational small talk or intermediate thinking. Keep observations highly factual, objective, and single-line.

Format each observation as a JSON object inside a list. Each object must have:
- "id": a unique 12-character lowercase hex string (generate deterministically or randomly)
- "content": the single-line factual description
- "timestamp": current time (YYYY-MM-DD HH:MM)
- "relevance": "low", "medium", "high", or "critical"
- "source_entry_ids": list of message IDs from the brackets [id] that support this observation
- "token_count": estimated token size of the observation content (approx 1 token per 4 characters)

Transcript Slice:
{formatted_transcript}

Respond ONLY with the JSON list. Do not add markdown backticks or preamble.
"""

    try:
        response = call_llm(
            task="compression",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        content_text = response.choices[0].message.content.strip()
        
        # Clean markdown wrappers if returned
        if content_text.startswith("```"):
            content_text = re.sub(r"^```[a-zA-Z]*\n", "", content_text)
            content_text = re.sub(r"\n```$", "", content_text)
            
        observations = json.loads(content_text.strip())
        if isinstance(observations, list) and observations:
            db.add_observations(session_id, observations)
            logger.info("OM: Extracted %d new observations", len(observations))
            
        # Update watermark to the latest processed message ID
        last_msg_id = messages[-1].get("id")
        if last_msg_id:
            db.update_observation_watermark(session_id, last_msg_id)
            
    except Exception as e:
        logger.warning("Observer pass failed: %s", e)


def _run_reflector(db: OMLedgerDB, session_id: str):
    """Call auxiliary LLM to distill active observations into reflections."""
    active_obs = db.get_active_observations(session_id)
    existing_ref = db.get_reflections(session_id)

    if not active_obs:
        return

    # Format for the reflector
    obs_text = "\n".join([f"- [{o['id']}] [{o['relevance']}] {o['content']}" for o in active_obs])
    ref_text = "\n".join([f"- [{r['id']}] {r['content']}" for r in existing_ref]) if existing_ref else "None"

    prompt = f"""You are the Reflector in an Observational Memory system. Your job is to distill new active observations and existing reflections into a concise, durable reflections ledger.

Reflections represent long-lived facts, user preferences, project constraints, architectural decisions, or stable environment characteristics.
Do not turn every temporary observation into a reflection. Only distill what is genuinely durable.
You can update, merge, or delete existing reflections if new evidence supersedes them.

Format each reflection as a JSON object inside a list. Each object must have:
- "id": a unique 12-character lowercase hex string
- "content": the single-line durable fact/preference/decision
- "supporting_observation_ids": list of active observation IDs that provide the evidence for this reflection
- "token_count": estimated token size of the reflection content (approx 1 token per 4 characters)

Existing Reflections:
{ref_text}

New Active Observations:
{obs_text}

Respond ONLY with the JSON list of ALL current reflections (both surviving old ones and new ones). Do not add markdown backticks.
"""

    try:
        response = call_llm(
            task="compression",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        content_text = response.choices[0].message.content.strip()
        
        # Clean markdown
        if content_text.startswith("```"):
            content_text = re.sub(r"^```[a-zA-Z]*\n", "", content_text)
            content_text = re.sub(r"\n```$", "", content_text)
            
        reflections = json.loads(content_text.strip())
        if isinstance(reflections, list):
            db.add_reflections(session_id, reflections)
            logger.info("OM: Updated reflections ledger. Total active reflections: %d", len(reflections))
            
            # Update watermark
            last_obs_id = active_obs[-1]["id"]
            db.update_reflection_watermark(session_id, last_obs_id)
            
    except Exception as e:
        logger.warning("Reflector pass failed: %s", e)


def _run_dropper(db: OMLedgerDB, session_id: str):
    """Identify and prune active observations that are now covered by reflections or are redundant."""
    active_obs = db.get_active_observations(session_id)
    reflections = db.get_reflections(session_id)

    if not active_obs:
        return

    # If the pool is small, no need to drop
    total_tokens = sum(o["token_count"] for o in active_obs)
    target_tokens = 10000  # V3 target pool size
    if total_tokens <= target_tokens:
        return

    obs_text = "\n".join([f"- [{o['id']}] {o['content']}" for o in active_obs])
    ref_text = "\n".join([f"- [{r['id']}] {r['content']} (Supports: {r['supporting_observation_ids']})" for r in reflections])

    prompt = f"""You are the Dropper in an Observational Memory system. Your job is to identify active observations that can be safely pruned (dropped) from active memory.

An active observation can be dropped if:
1. It is fully covered/distilled by one or more active reflections.
2. It has been superseded by a newer observation.
3. It is redundant or no longer relevant to the current state of work.

Format your response as a JSON object containing:
- "drop_observation_ids": list of observation IDs to drop.

Active Reflections:
{ref_text}

Active Observations Pool:
{obs_text}

Respond ONLY with the JSON object. Do not add markdown backticks.
"""

    try:
        response = call_llm(
            task="compression",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        content_text = response.choices[0].message.content.strip()
        
        # Clean markdown
        if content_text.startswith("```"):
            import re
            content_text = re.sub(r"^```[a-zA-Z]*\n", "", content_text)
            content_text = re.sub(r"\n```$", "", content_text)
            
        drop_data = json.loads(content_text.strip())
        to_drop = drop_data.get("drop_observation_ids", [])
        if to_drop:
            db.drop_observations(session_id, to_drop)
            logger.info("OM: Pruned %d observations from the active pool", len(to_drop))
            
    except Exception as e:
        logger.warning("Dropper pass failed: %s", e)
