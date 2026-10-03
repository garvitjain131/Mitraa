"""Path sandbox: every user- or model-supplied path goes through Sandbox.resolve()."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path

_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)
}

_SYSTEM_DIR_NAMES = {
    "windows", "system32", "syswow64", "program files", "program files (x86)",
    "programdata", "appdata", "$recycle.bin", "system volume information", "recovery",
}
_POSIX_SYSTEM_PREFIXES = (
    "/etc", "/bin", "/sbin", "/usr", "/lib", "/boot", "/dev", "/proc", "/sys",
    "/root", "/var", "/System", "/Library", "/private",
)

EXECUTABLE_SUFFIXES = {
    ".exe", ".bat", ".cmd", ".com", ".msi", ".msp", ".ps1", ".psm1", ".vbs", ".vbe",
    ".wsf", ".scr", ".dll", ".sys", ".cpl", ".reg", ".hta", ".jar", ".sh", ".app",
}

_SEPARATORS = re.compile(r"[\\/]+")


class SandboxViolation(Exception):
    """Raised when a path is not allowed.

    kind is "blocked" for paths outside the allowed roots or otherwise forbidden,
    and "invalid" for paths that are malformed (empty, relative without an alias).
    """

    def __init__(self, reason: str, kind: str = "blocked") -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind


class Sandbox:
    def __init__(self, roots: Iterable[Path | str]) -> None:
        self.roots: list[Path] = [Path(os.path.expanduser(str(r))).resolve(strict=False) for r in roots]
        if not self.roots:
            raise ValueError("Sandbox needs at least one allowed root")
        self._aliases = {root.name.lower(): root for root in self.roots}

    def describe_roots(self) -> str:
        return ", ".join(f"{root.name} ({root})" for root in self.roots)

    def resolve(self, text: str) -> Path:
        """Turn user/model text into an absolute path inside an allowed root, or raise."""
        raw = (text or "").strip().strip('"').strip("'").strip()
        if not raw:
            raise SandboxViolation("Empty path.", kind="invalid")
        if "\x00" in raw:
            raise SandboxViolation("Path contains a null byte.")

        candidate = self._apply_alias(os.path.expanduser(raw))
        if not candidate.is_absolute() and not candidate.root:  # "\etc" on Windows is rooted, not relative
            if ".." in _SEPARATORS.split(raw):
                raise SandboxViolation(f"'{text}' uses '..' to climb out of a folder; that is not allowed.")
            names = ", ".join(root.name for root in self.roots)
            raise SandboxViolation(
                f"'{text}' is not a full path. Use an absolute path or start with one of: {names}. "
                "Use search_files to find a file's location.",
                kind="invalid",
            )

        resolved = candidate.resolve(strict=False)
        root = self.root_for(resolved)
        if root is None:
            if self._looks_like_system_path(resolved):
                raise SandboxViolation(f"'{text}' is a system location. Mitraa never touches system folders.")
            raise SandboxViolation(
                f"'{text}' is outside Mitraa's permitted folders ({', '.join(r.name for r in self.roots)})."
            )
        self._check_components(resolved.relative_to(root).parts, text)
        return resolved

    def root_for(self, path: Path) -> Path | None:
        for root in self.roots:
            if path.is_relative_to(root):
                return root
        return None

    def contains(self, path: Path) -> bool:
        return self.root_for(path) is not None

    def is_root(self, path: Path) -> bool:
        return any(path == root for root in self.roots)

    def _apply_alias(self, raw: str) -> Path:
        path = Path(raw)
        if path.is_absolute():
            return path
        first, *rest = _SEPARATORS.split(raw, maxsplit=1) + [""]
        root = self._aliases.get(first.lower())
        if root is None:
            return path
        tail = rest[0] if rest else ""
        return root / tail if tail else root

    @staticmethod
    def _check_components(parts: tuple[str, ...], original: str) -> None:
        for part in parts:
            if sys.platform == "win32" and ":" in part:
                raise SandboxViolation(f"'{original}' contains ':' (alternate data streams are not allowed).")
            stem = part.split(".", 1)[0].rstrip(" ").upper()
            if stem in _WINDOWS_RESERVED:
                raise SandboxViolation(f"'{part}' is a reserved device name on Windows.")
            if part.lower() in _SYSTEM_DIR_NAMES:
                raise SandboxViolation(f"'{original}' points into a system folder ('{part}').")

    @staticmethod
    def _looks_like_system_path(path: Path) -> bool:
        if any(part.lower() in _SYSTEM_DIR_NAMES for part in path.parts):
            return True
        posix = path.as_posix()
        return any(posix == p or posix.startswith(p + "/") for p in _POSIX_SYSTEM_PREFIXES)


def is_executable(path: Path) -> bool:
    return path.suffix.lower() in EXECUTABLE_SUFFIXES
