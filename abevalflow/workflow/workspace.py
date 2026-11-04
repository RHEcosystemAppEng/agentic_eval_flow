"""Isolated source staging and durable run ownership."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from abevalflow.config.runtime import ConfigurationError, RuntimeConfig

EXCLUDED = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".abeval", ".konflux", "node_modules"}


def staged_source_root(config: RuntimeConfig) -> Path:
    """Preserve the skill directory name when the source is a standalone skill."""
    root = config.output / "source"
    return root / config.source.name if config.source == config.skill else root


@contextmanager
def run_lock(directory: Path, *, create: bool = False) -> Iterator[None]:
    """Reserve a run against concurrent writers without overwriting existing evidence."""
    if create:
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    elif not (directory / "run.json").is_file():
        raise ConfigurationError(f"Not an evaluation run: {directory}")
    lock = directory / ".lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ConfigurationError(f"Run is locked: {directory}") from exc
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def stage_source(config: RuntimeConfig) -> str:
    """Copy source and resolve internal links while retaining authored input identity."""
    root = config.source
    digest = hashlib.sha256()
    for base, directories, files in os.walk(root, followlinks=False):
        base_path = Path(base)
        directories[:] = sorted(d for d in directories if d not in EXCLUDED and not (base_path / d) == config.output)
        for name in sorted(directories + files):
            item = base_path / name
            if item == config.output or item.name in EXCLUDED:
                continue
            if item.is_symlink():
                target = item.resolve(strict=True)
                if not target.is_relative_to(root):
                    raise ConfigurationError(f"Source link leaves the source root: {item.relative_to(root)}")
                digest.update(f"link:{item.relative_to(root)}:{item.readlink()}".encode())
            elif item.is_file():
                digest.update(f"file:{item.relative_to(root)}:{item.stat().st_mode & 0o111}:".encode())
                digest.update(item.read_bytes())
    destination = staged_source_root(config)

    def ignore(directory: str, entries: list[str]) -> set[str]:
        return {e for e in entries if e in EXCLUDED or (Path(directory) / e).resolve() == config.output}

    shutil.copytree(root, destination, ignore=ignore, symlinks=False)
    return digest.hexdigest()
