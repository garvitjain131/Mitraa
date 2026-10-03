from __future__ import annotations

from pathlib import Path

import pytest

from mitraa.config import Settings
from mitraa.db.session import make_engine, make_session_factory
from mitraa.security.sandbox import Sandbox
from mitraa.tools import files as file_tools
from mitraa.tools.base import ToolContext
from mitraa.tools.registry import build_default_registry


@pytest.fixture
def roots(tmp_path: Path) -> dict[str, Path]:
    base = tmp_path / "home"
    out = {}
    for name in ("Documents", "Downloads", "Desktop"):
        folder = base / name
        folder.mkdir(parents=True)
        out[name] = folder.resolve()
    return out


@pytest.fixture
def sandbox(roots: dict[str, Path]) -> Sandbox:
    return Sandbox(roots.values())


@pytest.fixture
def settings(roots: dict[str, Path], tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        mitraa_allowed_roots=";".join(str(p) for p in roots.values()),
        mitraa_db_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        mitraa_max_steps=8,
        mitraa_bulk_threshold=5,
    )


@pytest.fixture
def session_factory(settings: Settings):
    return make_session_factory(make_engine(settings.mitraa_db_url))


@pytest.fixture
def session(session_factory):
    s = session_factory()
    yield s
    s.close()


@pytest.fixture
def trash(tmp_path: Path, monkeypatch) -> list[Path]:
    """Replace send2trash with a fake that moves items into tmp_path/_trash."""
    bin_dir = tmp_path / "_trash"
    bin_dir.mkdir()
    trashed: list[Path] = []

    def fake_send2trash(path: str) -> None:
        src = Path(path)
        trashed.append(src)
        src.rename(bin_dir / f"{len(trashed)}_{src.name}")

    monkeypatch.setattr(file_tools, "send2trash", fake_send2trash)
    return trashed


@pytest.fixture
def registry():
    return build_default_registry()


@pytest.fixture
def make_ctx(session, sandbox, settings, registry):
    from mitraa.db.models import Conversation

    conv = Conversation(title="test")
    session.add(conv)
    session.commit()

    def _make(approved: bool = False, audit_id: str = "g1") -> ToolContext:
        return ToolContext(
            session=session,
            conversation_id=conv.id,
            sandbox=sandbox,
            settings=settings,
            registry=registry,
            audit_id=audit_id,
            approved=approved,
        )

    return _make
