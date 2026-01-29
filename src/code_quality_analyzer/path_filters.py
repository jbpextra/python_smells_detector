"""Utilities for filtering files and directories using git-style pathspecs."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, List, Optional

from pathspec import PathSpec

logger = logging.getLogger(__name__)


def _normalize_patterns(lines: Iterable[str]) -> List[str]:
    """Return cleaned patterns, ignoring blank lines and comments."""

    patterns: List[str] = []
    for raw in lines:
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        patterns.append(pattern)
    return patterns


def compile_pathspec(
    inline_patterns: Optional[Iterable[str]] = None,
    pattern_files: Optional[Iterable[str]] = None,
) -> Optional[PathSpec]:
    """Build a PathSpec matcher from inline patterns and/or files."""

    collected: List[str] = []

    for file_path in pattern_files or []:
        path = Path(file_path).expanduser()
        if not path.is_file():
            logger.warning("Pathspec file '%s' not found; skipping.", path)
            continue

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("Failed to read pathspec file '%s': %s", path, exc)
            continue

        collected.extend(_normalize_patterns(lines))

    if inline_patterns:
        collected.extend(_normalize_patterns(inline_patterns))

    if not collected:
        return None

    return PathSpec.from_lines("gitwildmatch", collected)


def should_ignore(pathspec: Optional[PathSpec], base_dir: str, candidate_path: str) -> bool:
    """Return True if the candidate path matches the compiled pathspec."""

    if pathspec is None:
        return False

    rel_path = os.path.relpath(candidate_path, base_dir)
    rel_path = rel_path.replace(os.sep, "/")

    if rel_path in (".", ""):
        rel_path = ""

    return pathspec.match_file(rel_path) or pathspec.match_file(f"{rel_path}/")


def filter_child_directories(
    directories: List[str],
    root: str,
    base_dir: str,
    pathspec: Optional[PathSpec],
) -> None:
    """Remove directories that should be ignored from an os.walk directories list."""

    if pathspec is None:
        return

    keep: List[str] = []
    for directory in directories:
        candidate = os.path.join(root, directory)
        if should_ignore(pathspec, base_dir, candidate):
            continue
        keep.append(directory)

    directories[:] = keep
