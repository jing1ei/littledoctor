"""Safe local writes and directory boundaries for generated health data."""
from __future__ import annotations

import os
import tempfile
import shutil
from contextlib import contextmanager
from pathlib import Path


def atomic_write(path: Path, text: str) -> None:
    """Publish a complete UTF-8 file with owner-only permissions.

    A same-directory temporary file keeps replacement on the same filesystem.
    An interrupted write leaves the previously published file intact.
    """
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"refusing to replace a symbolic link: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def disjoint(a: Path, b: Path) -> None:
    """Reject equal or nested input/output directories, including symlink aliases."""
    a, b = Path(a).resolve(), Path(b).resolve()
    if a == b or a in b.parents or b in a.parents:
        raise ValueError(f"folders must not overlap: {a} and {b}")


def atomic_copy(source: Path, destination: Path) -> None:
    """Copy an asset without truncating an existing file or hard-linked source."""
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def reject_links(root: Path) -> None:
    """Do not let asset mirroring write through or recursively follow symlinks."""
    root = Path(root)
    if root.is_symlink():
        raise ValueError(f"symbolic link is not a managed asset folder: {root}")
    if root.exists():
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"symbolic link inside asset folder: {path}")


@contextmanager
def output_lock(directory: Path):
    """Prevent concurrent CLI writers; OS locks are released even after a crash."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".littledocter.lock"
    if path.is_symlink():
        raise ValueError(f"lock file must not be a symbolic link: {path}")
    with open(path, "a+b") as stream:
        if os.name == "posix":
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError(f"another littledocter command is writing to {directory}") from exc
        else:
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError(f"another littledocter command is writing to {directory}") from exc
        try:
            yield
        finally:
            if os.name == "posix":
                fcntl.flock(stream, fcntl.LOCK_UN)
            else:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
