from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from mitraa.security.sandbox import Sandbox, SandboxViolation


def test_alias_is_case_insensitive(sandbox: Sandbox, roots):
    assert sandbox.resolve("documents/resume.pdf") == roots["Documents"] / "resume.pdf"
    assert sandbox.resolve("DOWNLOADS\\a\\b.txt") == roots["Downloads"] / "a" / "b.txt"
    assert sandbox.resolve("Desktop") == roots["Desktop"]


def test_absolute_path_inside_root(sandbox: Sandbox, roots):
    target = roots["Documents"] / "notes.txt"
    assert sandbox.resolve(str(target)) == target


def test_quotes_and_whitespace_are_stripped(sandbox: Sandbox, roots):
    assert sandbox.resolve('  "Documents/x.txt"  ') == roots["Documents"] / "x.txt"


def test_tilde_expansion(tmp_path: Path, monkeypatch):
    home = tmp_path / "userhome"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    sb = Sandbox(["~/Documents"])
    assert sb.resolve("~/Documents/a.txt") == (home / "Documents" / "a.txt").resolve()


@pytest.mark.parametrize(
    "text",
    [
        "Documents/../../outside.txt",
        "Documents\\..\\..\\..\\Windows",
        "..\\..\\",
        "../../etc/passwd",
        "C:\\Windows\\System32\\drivers\\etc\\hosts",
        "C:/Windows/System32",
        "/etc/passwd",
        "C:\\Program Files\\app\\app.exe",
        "\\\\server\\share\\file.txt",
    ],
)
def test_escapes_are_blocked(sandbox: Sandbox, text: str):
    with pytest.raises(SandboxViolation) as exc:
        sandbox.resolve(text)
    assert exc.value.kind == "blocked"


def test_sibling_prefix_is_not_inside(sandbox: Sandbox, roots):
    sneaky = roots["Documents"].parent / (roots["Documents"].name + "2") / "x.txt"
    with pytest.raises(SandboxViolation):
        sandbox.resolve(str(sneaky))


def test_system_folder_message(sandbox: Sandbox):
    with pytest.raises(SandboxViolation, match="system"):
        sandbox.resolve("C:\\Windows\\System32\\config")


@pytest.mark.parametrize("name", ["CON", "nul.txt", "COM1.log", "lpt3"])
def test_reserved_windows_names(sandbox: Sandbox, name: str):
    with pytest.raises(SandboxViolation, match="reserved"):
        sandbox.resolve(f"Documents/{name}")


@pytest.mark.skipif(sys.platform != "win32", reason="ADS is Windows-only")
def test_alternate_data_stream_rejected(sandbox: Sandbox):
    with pytest.raises(SandboxViolation):
        sandbox.resolve("Documents/a.txt:hidden")


@pytest.mark.parametrize("text", ["", "   ", "resume.pdf", "some/relative/path.txt"])
def test_relative_or_empty_is_invalid(sandbox: Sandbox, text: str):
    with pytest.raises(SandboxViolation) as exc:
        sandbox.resolve(text)
    assert exc.value.kind == "invalid"


def test_symlink_escape_rejected(sandbox: Sandbox, roots, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x")
    link = roots["Documents"] / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")
    with pytest.raises(SandboxViolation):
        sandbox.resolve("Documents/link/secret.txt")


def test_symlink_inside_root_allowed(sandbox: Sandbox, roots):
    real = roots["Documents"] / "real"
    real.mkdir()
    link = roots["Documents"] / "alias"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")
    assert sandbox.resolve("Documents/alias/x.txt") == real / "x.txt"


def test_is_root(sandbox: Sandbox, roots):
    assert sandbox.is_root(sandbox.resolve("Documents"))
    assert not sandbox.is_root(sandbox.resolve("Documents/sub"))


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_junction_escape_rejected(sandbox: Sandbox, roots, tmp_path: Path):
    import _winapi

    outside = tmp_path / "outside_j"
    outside.mkdir()
    _winapi.CreateJunction(str(outside), str(roots["Documents"] / "jlink"))
    with pytest.raises(SandboxViolation):
        sandbox.resolve("Documents/jlink/secret.txt")
