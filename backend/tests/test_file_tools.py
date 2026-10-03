from __future__ import annotations

import os
from pathlib import Path

import pytest

from mitraa.tools import files as ft


def _touch(path: Path, text: str = "hello") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ---------------------------------------------------------------- search / list / read

def test_search_case_insensitive_across_roots(make_ctx, roots):
    _touch(roots["Documents"] / "Resume.pdf")
    _touch(roots["Desktop"] / "old" / "resume_v2.docx")
    _touch(roots["Downloads"] / "unrelated.txt")
    result = ft.SearchFiles().run(ft.SearchFilesArgs(query="resume"), make_ctx())
    names = [r["name"] for r in result.data["results"]]
    assert result.ok and set(names) == {"Resume.pdf", "resume_v2.docx"}


def test_search_exact_match_ranks_first(make_ctx, roots):
    _touch(roots["Documents"] / "my_resume_notes.txt")
    _touch(roots["Documents"] / "resume.pdf")
    result = ft.SearchFiles().run(ft.SearchFilesArgs(query="resume.pdf"), make_ctx())
    assert result.data["results"][0]["name"] == "resume.pdf"


def test_search_extension_filter_and_folder(make_ctx, roots):
    _touch(roots["Documents"] / "a.pdf")
    _touch(roots["Documents"] / "a.txt")
    _touch(roots["Downloads"] / "a.pdf")
    args = ft.SearchFilesArgs(query="a", folder="Documents", extensions=["PDF"])
    names = [r["path"] for r in ft.SearchFiles().run(args, make_ctx()).data["results"]]
    assert names == [str(roots["Documents"] / "a.pdf")]


def test_search_fuzzy_and_tokens(make_ctx, roots):
    _touch(roots["Documents"] / "pitch_deck_final.pptx")
    ctx = make_ctx()
    assert ft.SearchFiles().run(ft.SearchFilesArgs(query="pitch deck"), ctx).data["results"]
    assert ft.SearchFiles().run(ft.SearchFilesArgs(query="pitch_dek_final"), ctx).data["results"]


def test_search_skips_hidden_and_tool_folders(make_ctx, roots):
    _touch(roots["Documents"] / ".secret" / "resume.pdf")
    _touch(roots["Documents"] / "node_modules" / "resume.js")
    result = ft.SearchFiles().run(ft.SearchFilesArgs(query="resume"), make_ctx())
    assert result.data["results"] == []


def test_search_limit(make_ctx, roots):
    for i in range(10):
        _touch(roots["Documents"] / f"note{i}.txt")
    result = ft.SearchFiles().run(ft.SearchFilesArgs(query="note", limit=3), make_ctx())
    assert len(result.data["results"]) == 3


def test_list_folder(make_ctx, roots):
    _touch(roots["Documents"] / "b.txt")
    (roots["Documents"] / "sub").mkdir()
    result = ft.ListFolder().run(ft.ListFolderArgs(path="Documents"), make_ctx())
    assert [i["name"] for i in result.data["items"]] == ["sub", "b.txt"]


def test_read_text_file(make_ctx, roots):
    _touch(roots["Documents"] / "n.md", "x" * 50)
    result = ft.ReadFile().run(ft.ReadFileArgs(path="Documents/n.md", max_chars=10), make_ctx())
    assert result.ok and result.data["content"] == "x" * 10 and result.data["truncated"]


def test_read_binary_returns_metadata(make_ctx, roots):
    (roots["Documents"] / "img.dat").write_bytes(b"\x89PNG\x00\x00binary")
    result = ft.ReadFile().run(ft.ReadFileArgs(path="Documents/img.dat"), make_ctx())
    assert result.data["binary"] is True and "content" not in result.data


# ---------------------------------------------------------------- move / copy / rename

def test_move_into_folder_keeps_name(make_ctx, roots):
    src = _touch(roots["Documents"] / "resume.pdf")
    result = ft.MoveFile().run(ft.MoveFileArgs(source="Documents/resume.pdf", destination="Downloads"), make_ctx())
    assert result.ok
    assert not src.exists() and (roots["Downloads"] / "resume.pdf").exists()
    assert result.data["destination"] == str(roots["Downloads"] / "resume.pdf")


def test_move_with_new_name(make_ctx, roots):
    _touch(roots["Documents"] / "a.txt")
    ft.MoveFile().run(ft.MoveFileArgs(source="Documents/a.txt", destination="Desktop/b.txt"), make_ctx())
    assert (roots["Desktop"] / "b.txt").exists()


def test_move_missing_source(make_ctx):
    result = ft.MoveFile().run(ft.MoveFileArgs(source="Documents/nope.pdf", destination="Downloads"), make_ctx())
    assert not result.ok and "search_files" in result.message


def test_move_missing_destination_parent(make_ctx, roots):
    _touch(roots["Documents"] / "a.txt")
    result = ft.MoveFile().run(ft.MoveFileArgs(source="Documents/a.txt", destination="Downloads/x/y/a.txt"), make_ctx())
    assert not result.ok and "does not exist" in result.message


def test_move_never_overwrites_without_approval(make_ctx, roots):
    _touch(roots["Documents"] / "a.txt", "new")
    _touch(roots["Downloads"] / "a.txt", "old")
    result = ft.MoveFile().run(ft.MoveFileArgs(source="Documents/a.txt", destination="Downloads"), make_ctx())
    assert not result.ok
    assert (roots["Downloads"] / "a.txt").read_text() == "old"


def test_move_overwrite_with_approval_trashes_old(make_ctx, roots, trash):
    _touch(roots["Documents"] / "a.txt", "new")
    _touch(roots["Downloads"] / "a.txt", "old")
    result = ft.MoveFile().run(ft.MoveFileArgs(source="Documents/a.txt", destination="Downloads"), make_ctx(approved=True))
    assert result.ok
    assert (roots["Downloads"] / "a.txt").read_text() == "new"
    assert trash == [roots["Downloads"] / "a.txt"]


def test_move_folder_into_itself(make_ctx, roots):
    (roots["Documents"] / "proj").mkdir()
    result = ft.MoveFile().run(ft.MoveFileArgs(source="Documents/proj", destination="Documents/proj"), make_ctx())
    assert not result.ok


def test_copy_file(make_ctx, roots):
    _touch(roots["Documents"] / "a.txt")
    result = ft.CopyFile().run(ft.CopyFileArgs(source="Documents/a.txt", destination="Desktop"), make_ctx())
    assert result.ok and (roots["Documents"] / "a.txt").exists() and (roots["Desktop"] / "a.txt").exists()


def test_copy_folder(make_ctx, roots):
    _touch(roots["Documents"] / "proj" / "x.txt")
    ft.CopyFile().run(ft.CopyFileArgs(source="Documents/proj", destination="Desktop"), make_ctx())
    assert (roots["Desktop"] / "proj" / "x.txt").exists()


def test_rename_file(make_ctx, roots):
    _touch(roots["Documents"] / "a.txt")
    result = ft.RenameFile().run(ft.RenameFileArgs(path="Documents/a.txt", new_name="b.txt"), make_ctx())
    assert result.ok and (roots["Documents"] / "b.txt").exists()


def test_rename_rejects_path_in_name():
    with pytest.raises(ValueError):
        ft.RenameFileArgs(path="Documents/a.txt", new_name="../b.txt")


def test_create_folder_and_existing(make_ctx, roots):
    ctx = make_ctx()
    result = ft.CreateFolder().run(ft.CreateFolderArgs(path="Documents/a/b"), ctx)
    assert result.ok and (roots["Documents"] / "a" / "b").is_dir()
    again = ft.CreateFolder().run(ft.CreateFolderArgs(path="Documents/a/b"), ctx)
    assert again.ok and again.data["created"] is False


def test_delete_uses_recycle_bin(make_ctx, roots, trash):
    target = _touch(roots["Documents"] / "old_notes.txt")
    result = ft.DeleteFile().run(ft.DeleteFileArgs(path="Documents/old_notes.txt"), make_ctx(approved=True))
    assert result.ok and not target.exists() and trash == [target]
    assert "Recycle Bin" in result.message


def test_move_files_bulk(make_ctx, roots):
    for i in range(3):
        _touch(roots["Documents"] / f"f{i}.txt")
    args = ft.MoveFilesArgs(sources=[f"Documents/f{i}.txt" for i in range(3)] + ["Documents/missing.txt"], destination_folder="Desktop")
    result = ft.MoveFiles().run(args, make_ctx())
    assert result.ok and len(result.data["moved"]) == 3 and len(result.data["errors"]) == 1


def test_no_os_remove_in_tool_code():
    source = Path(ft.__file__).read_text()
    for banned in ("os.remove", "os.unlink", ".unlink(", "rmtree", "os.rmdir"):
        assert banned not in source


# ---------------------------------------------------------------- undo

def test_undo_move(make_ctx, roots):
    ctx = make_ctx()
    _touch(roots["Documents"] / "resume.pdf")
    ft.MoveFile().run(ft.MoveFileArgs(source="Documents/resume.pdf", destination="Downloads"), ctx)
    result = ft.UndoLastAction().run(ft.UndoArgs(), ctx)
    assert result.ok and (roots["Documents"] / "resume.pdf").exists()
    assert not (roots["Downloads"] / "resume.pdf").exists()
    assert not ft.UndoLastAction().run(ft.UndoArgs(), ctx).ok  # nothing left


def test_undo_reverts_most_recent_group_only(make_ctx, roots):
    _touch(roots["Documents"] / "a.txt")
    _touch(roots["Documents"] / "b.txt")
    ft.MoveFile().run(ft.MoveFileArgs(source="Documents/a.txt", destination="Desktop"), make_ctx(audit_id="g1"))
    ft.MoveFiles().run(ft.MoveFilesArgs(sources=["Documents/b.txt"], destination_folder="Downloads"), make_ctx(audit_id="g2"))
    ft.UndoLastAction().run(ft.UndoArgs(), make_ctx())
    assert (roots["Documents"] / "b.txt").exists()
    assert (roots["Desktop"] / "a.txt").exists()


def test_undo_copy_and_create(make_ctx, roots, trash):
    _touch(roots["Documents"] / "a.txt")
    ft.CreateFolder().run(ft.CreateFolderArgs(path="Documents/new/deep"), make_ctx(audit_id="g1"))
    ft.CopyFile().run(ft.CopyFileArgs(source="Documents/a.txt", destination="Desktop"), make_ctx(audit_id="g2"))
    assert ft.UndoLastAction().run(ft.UndoArgs(), make_ctx()).ok
    assert not (roots["Desktop"] / "a.txt").exists() and (roots["Documents"] / "a.txt").exists()
    assert ft.UndoLastAction().run(ft.UndoArgs(), make_ctx()).ok
    assert not (roots["Documents"] / "new").exists()


def test_undo_refuses_to_overwrite(make_ctx, roots):
    _touch(roots["Documents"] / "a.txt", "1")
    ft.MoveFile().run(ft.MoveFileArgs(source="Documents/a.txt", destination="Desktop"), make_ctx())
    _touch(roots["Documents"] / "a.txt", "2")
    result = ft.UndoLastAction().run(ft.UndoArgs(), make_ctx())
    assert not result.ok and (roots["Documents"] / "a.txt").read_text() == "2"


def test_undo_delete_explains_recycle_bin(make_ctx, roots, trash):
    _touch(roots["Documents"] / "x.txt")
    ft.DeleteFile().run(ft.DeleteFileArgs(path="Documents/x.txt"), make_ctx())
    result = ft.UndoLastAction().run(ft.UndoArgs(), make_ctx())
    assert not result.ok and "Recycle Bin" in result.message


def test_trash_helper_uses_send2trash(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(ft, "send2trash", lambda p: called.append(p))
    ft._trash(tmp_path / "x")
    assert called == [str(tmp_path / "x")]
    assert os.path.exists(tmp_path)  # nothing was actually removed


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows-only")
def test_search_does_not_follow_junctions_out_of_sandbox(make_ctx, roots, tmp_path):
    import _winapi

    outside = tmp_path / "outside_s"
    _touch(outside / "secret_resume.pdf")
    _winapi.CreateJunction(str(outside), str(roots["Documents"] / "jlink"))
    result = ft.SearchFiles().run(ft.SearchFilesArgs(query="secret"), make_ctx())
    assert result.data["results"] == []
