"""Engine and session factory."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from mitraa.config import get_settings
from mitraa.db.models import Base


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@lru_cache
def default_session_factory() -> sessionmaker[Session]:
    return make_session_factory(make_engine(get_settings().mitraa_db_url))


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = default_session_factory()()
    try:
        yield session
    finally:
        session.close()
