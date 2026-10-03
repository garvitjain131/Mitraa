from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mitraa.db.models import Memory, utcnow
from mitraa.memory.store import list_memories

STOP = {"a", "an", "the", "i", "my", "me", "what", "is", "are", "am", "in", "on", "to", "for",
        "of", "and", "do", "did", "you", "we", "our", "it", "this", "that", "about", "how"}


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower())) - STOP


def top_k(session: Session, message: str, k: int = 8) -> list[Memory]:
    query = words(message)
    now = utcnow()

    def score(row: Memory) -> float:
        tokens = words(f"{row.key} {row.value}")
        overlap = len(query & tokens)
        updated = row.updated_at.replace(tzinfo=timezone.utc) if row.updated_at.tzinfo is None else row.updated_at
        recency = 1 / (1 + max(0, (now-updated).days) / 30)
        boost = 1 if row.type == "project" and query & {"project", "task", "plan", "building", "build"} else 0
        return overlap + 0.3 * recency + boost

    rows = list_memories(session)
    current = [m for m in rows if m.type == "project" and m.key == "current_project"]
    ranked = sorted(rows, key=lambda m: (score(m), m.updated_at), reverse=True)
    selected = (current + [m for m in ranked if m not in current])[:k]
    for row in selected:
        row.last_used_at = now
    session.flush()
    return selected
