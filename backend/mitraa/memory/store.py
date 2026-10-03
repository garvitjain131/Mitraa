from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mitraa.db.models import Memory, utcnow

TYPES = {"preference", "goal", "project", "fact", "decision"}


def remember(session: Session, type: str, key: str, value: str, source_message_id: int | None = None) -> Memory:
    if type not in TYPES:
        raise ValueError(f"Invalid memory type: {type}")
    key = key.strip()
    value = value.strip()
    if not key or not value:
        raise ValueError("Memory key and value are required.")
    row = session.scalar(select(Memory).where(Memory.type == type, Memory.key == key))
    if row is None:
        row = Memory(type=type, key=key, value=value, source_message_id=source_message_id)
        session.add(row)
    else:
        row.value = value
        row.updated_at = utcnow()
        row.source_message_id = source_message_id
    session.flush()
    return row


def list_memories(session: Session, type: str | None = None) -> list[Memory]:
    query = select(Memory).order_by(Memory.updated_at.desc())
    if type:
        query = query.where(Memory.type == type)
    return list(session.scalars(query))


def matches(session: Session, query: str) -> list[Memory]:
    q = query.lower().strip()
    return [m for m in list_memories(session) if q in m.key.lower() or q in m.value.lower()]


def as_dict(m: Memory) -> dict:
    return {"id": m.id, "type": m.type, "key": m.key, "value": m.value,
            "source_message_id": m.source_message_id, "created_at": m.created_at.isoformat(),
            "updated_at": m.updated_at.isoformat(),
            "last_used_at": m.last_used_at.isoformat() if m.last_used_at else None}
