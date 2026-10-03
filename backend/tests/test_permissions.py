from __future__ import annotations

from pathlib import Path

from mitraa.security.permissions import evaluate
from mitraa.tools import files as ft
from mitraa.tools.base import RiskLevel


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    return path


def test_read_only_tools_are_low(make_ctx):
    ctx = make_ctx()
    assert evaluate(ft.SearchFiles(), ft.SearchFilesArgs(query="x"), ctx).level == RiskLevel.LOW
    assert evaluate(ft.ListFolder(), ft.ListFolderArgs(path="Documents"), ctx).level == RiskLevel.LOW
    assert evaluate(ft.ReadFile(), ft.ReadFileArgs(path="Documents/a.txt"), ctx).level == RiskLevel.LOW


def test_single_move_is_medium(make_ctx, roots):
    _touch(roots["Documents"] / "a.pdf")
    a = evaluate(ft.MoveFile(), ft.MoveFileArgs(source="Documents/a.pdf", destination="Downloads"), make_ctx())
    assert a.level == RiskLevel.MEDIUM and not a.needs_approval


def test_overwrite_escalates_to_high(make_ctx, roots):
    _touch(roots["Documents"] / "a.pdf")
    _touch(roots["Downloads"] / "a.pdf")
    a = evaluate(ft.MoveFile(), ft.MoveFileArgs(source="Documents/a.pdf", destination="Downloads"), make_ctx())
    assert a.level == RiskLevel.HIGH and any("replace" in r for r in a.reasons)
    c = evaluate(ft.CopyFile(), ft.CopyFileArgs(source="Documents/a.pdf", destination="Downloads"), make_ctx())
    assert c.level == RiskLevel.HIGH


def test_delete_is_always_high(make_ctx, roots):
    _touch(roots["Documents"] / "a.txt")
    assert evaluate(ft.DeleteFile(), ft.DeleteFileArgs(path="Documents/a.txt"), make_ctx()).needs_approval


def test_bulk_threshold(make_ctx, roots):
    paths = [str(_touch(roots["Documents"] / f"f{i}.txt")) for i in range(6)]
    ctx = make_ctx()
    five = evaluate(ft.MoveFiles(), ft.MoveFilesArgs(sources=paths[:5], destination_folder="Desktop"), ctx)
    six = evaluate(ft.MoveFiles(), ft.MoveFilesArgs(sources=paths, destination_folder="Desktop"), ctx)
    assert five.level == RiskLevel.MEDIUM
    assert six.level == RiskLevel.HIGH


def test_moving_a_big_folder_is_bulk(make_ctx, roots):
    for i in range(7):
        _touch(roots["Documents"] / "proj" / f"{i}.txt")
    a = evaluate(ft.MoveFile(), ft.MoveFileArgs(source="Documents/proj", destination="Desktop"), make_ctx())
    assert a.level == RiskLevel.HIGH


def test_outside_sandbox_is_blocked(make_ctx):
    a = evaluate(ft.MoveFile(), ft.MoveFileArgs(source="C:\\Windows\\System32\\drivers\\etc\\hosts", destination="Downloads"), make_ctx())
    assert a.level == RiskLevel.BLOCKED
    b = evaluate(ft.ListFolder(), ft.ListFolderArgs(path="..\\..\\"), make_ctx())
    assert b.level == RiskLevel.BLOCKED


def test_executables_are_blocked(make_ctx, roots):
    _touch(roots["Downloads"] / "setup.exe")
    _touch(roots["Downloads"] / "notes.txt")
    ctx = make_ctx()
    assert evaluate(ft.MoveFile(), ft.MoveFileArgs(source="Downloads/setup.exe", destination="Desktop"), ctx).level == RiskLevel.BLOCKED
    assert evaluate(ft.RenameFile(), ft.RenameFileArgs(path="Downloads/notes.txt", new_name="notes.bat"), ctx).level == RiskLevel.BLOCKED
    assert evaluate(ft.DeleteFile(), ft.DeleteFileArgs(path="Downloads/setup.exe"), ctx).level == RiskLevel.BLOCKED


def test_root_folders_are_protected(make_ctx):
    a = evaluate(ft.DeleteFile(), ft.DeleteFileArgs(path="Documents"), make_ctx())
    assert a.level == RiskLevel.BLOCKED


def test_relative_path_is_an_error_not_a_block(make_ctx):
    a = evaluate(ft.MoveFile(), ft.MoveFileArgs(source="resume.pdf", destination="Downloads"), make_ctx())
    assert a.error and "search_files" in a.error and a.level != RiskLevel.BLOCKED
