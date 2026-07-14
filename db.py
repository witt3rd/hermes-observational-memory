"""SQLite helper for the Observational Memory Engine's ledger storage."""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class OMLedgerDB:
    """Manages the SQLite database for observations, reflections, and watermarks."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            
            # Sessions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    last_prompt_tokens INTEGER DEFAULT 0,
                    last_completion_tokens INTEGER DEFAULT 0,
                    last_total_tokens INTEGER DEFAULT 0,
                    compression_count INTEGER DEFAULT 0
                );
            """)

            # Observations table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    content TEXT,
                    timestamp TEXT,
                    relevance TEXT,
                    source_entry_ids TEXT, -- JSON array of message IDs or turn numbers
                    token_count INTEGER,
                    dropped INTEGER DEFAULT 0,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );
            """)

            # Reflections table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reflections (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    content TEXT,
                    supporting_observation_ids TEXT, -- JSON array of observation IDs
                    token_count INTEGER,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );
            """)

            # Watermarks and progress tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS watermarks (
                    session_id TEXT PRIMARY KEY,
                    last_observed_id TEXT,
                    last_reflected_id TEXT,
                    raw_tokens_since_observation INTEGER DEFAULT 0,
                    raw_tokens_since_reflection INTEGER DEFAULT 0,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );
            """)
            conn.commit()

    # -- Session Operations ------------------------------------------------

    def ensure_session(self, session_id: str):
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id) VALUES (?);",
                (session_id,)
            )
            conn.execute(
                "INSERT OR IGNORE INTO watermarks (session_id) VALUES (?);",
                (session_id,)
            )
            conn.commit()

    def get_session_stats(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?;",
                (session_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_session_tokens(self, session_id: str, prompt: int, completion: int, total: int):
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE sessions 
                SET last_prompt_tokens = ?, last_completion_tokens = ?, last_total_tokens = ?
                WHERE session_id = ?;
            """, (prompt, completion, total, session_id))
            conn.commit()

    def increment_compression_count(self, session_id: str):
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE sessions SET compression_count = compression_count + 1 WHERE session_id = ?;
            """, (session_id,))
            conn.commit()

    def reset_session(self, session_id: str):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM observations WHERE session_id = ?;", (session_id,))
            conn.execute("DELETE FROM reflections WHERE session_id = ?;", (session_id,))
            conn.execute("""
                UPDATE sessions 
                SET last_prompt_tokens = 0, last_completion_tokens = 0, last_total_tokens = 0, compression_count = 0
                WHERE session_id = ?;
            """, (session_id,))
            conn.execute("""
                UPDATE watermarks 
                SET last_observed_id = NULL, last_reflected_id = NULL, 
                    raw_tokens_since_observation = 0, raw_tokens_since_reflection = 0
                WHERE session_id = ?;
            """, (session_id,))
            conn.commit()

    # -- Observation Operations --------------------------------------------

    def add_observations(self, session_id: str, observations: List[Dict[str, Any]]):
        with self._get_conn() as conn:
            for obs in observations:
                conn.execute("""
                    INSERT OR REPLACE INTO observations 
                    (id, session_id, content, timestamp, relevance, source_entry_ids, token_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                """, (
                    obs["id"], session_id, obs["content"], obs["timestamp"],
                    obs["relevance"], json.dumps(obs["source_entry_ids"]), obs["token_count"]
                ))
            conn.commit()

    def get_active_observations(self, session_id: str) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM observations 
                WHERE session_id = ? AND dropped = 0;
            """, (session_id,)).fetchall()
            
            result = []
            for r in rows:
                d = dict(r)
                d["source_entry_ids"] = json.loads(d["source_entry_ids"])
                result.append(d)
            return result

    def drop_observations(self, session_id: str, observation_ids: List[str]):
        with self._get_conn() as conn:
            for obs_id in observation_ids:
                conn.execute("""
                    UPDATE observations SET dropped = 1 WHERE session_id = ? AND id = ?;
                """, (session_id, obs_id))
            conn.commit()

    # -- Reflection Operations ---------------------------------------------

    def add_reflections(self, session_id: str, reflections: List[Dict[str, Any]]):
        with self._get_conn() as conn:
            for ref in reflections:
                conn.execute("""
                    INSERT OR REPLACE INTO reflections 
                    (id, session_id, content, supporting_observation_ids, token_count)
                    VALUES (?, ?, ?, ?, ?);
                """, (
                    ref["id"], session_id, ref["content"],
                    json.dumps(ref["supporting_observation_ids"]), ref["token_count"]
                ))
            conn.commit()

    def get_reflections(self, session_id: str) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reflections WHERE session_id = ?;",
                (session_id,)
            ).fetchall()
            
            result = []
            for r in rows:
                d = dict(r)
                d["supporting_observation_ids"] = json.loads(d["supporting_observation_ids"])
                result.append(d)
            return result

    # -- Progress and Watermark Operations ---------------------------------

    def get_watermarks(self, session_id: str) -> Dict[str, Any]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM watermarks WHERE session_id = ?;",
                (session_id,)
            ).fetchone()
            return dict(row) if row else {}

    def update_clocks(self, session_id: str, raw_obs: int, raw_ref: int):
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE watermarks 
                SET raw_tokens_since_observation = ?, raw_tokens_since_reflection = ?
                WHERE session_id = ?;
            """, (raw_obs, raw_ref, session_id))
            conn.commit()

    def update_observation_watermark(self, session_id: str, last_msg_id: str):
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE watermarks SET last_observed_id = ?, raw_tokens_since_observation = 0
                WHERE session_id = ?;
            """, (last_msg_id, session_id))
            conn.commit()

    def update_reflection_watermark(self, session_id: str, last_obs_id: str):
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE watermarks SET last_reflected_id = ?, raw_tokens_since_reflection = 0
                WHERE session_id = ?;
            """, (last_obs_id, session_id))
            conn.commit()
