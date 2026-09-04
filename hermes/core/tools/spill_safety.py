"""Symlink-safe helpers for bounded terminal-output spill files."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import IO


_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def ensure_spill_dir(path: Path, *, private: bool = True) -> Path:
    """Create a spill directory and reject a symlink/non-directory leaf."""
    path = Path(path)
    path.mkdir(mode=0o700 if private else 0o777, parents=True, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise OSError("spill path is not a directory")
    if private and stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path


def open_exclusive(
    path: Path,
    *,
    private: bool = True,
    overwrite: bool = False,
    encoding: str = "utf-8",
    errors: str = "strict",
) -> IO[str]:
    """Open a file with exclusive creation so planted links are not followed."""
    path = Path(path)
    if overwrite:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISDIR(info.st_mode):
                raise OSError("refusing to overwrite a directory")
            os.unlink(path)
    mode = 0o600 if private else 0o666
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW,
        mode,
    )
    try:
        return os.fdopen(fd, "w", encoding=encoding, errors=errors)
    except Exception:
        os.close(fd)
        raise


def write_text_exclusive(
    path: Path,
    text: str,
    *,
    private: bool = True,
    overwrite: bool = False,
    encoding: str = "utf-8",
    errors: str = "strict",
) -> None:
    """Write text without following a pre-existing symlink."""
    with open_exclusive(
        path,
        private=private,
        overwrite=overwrite,
        encoding=encoding,
        errors=errors,
    ) as handle:
        handle.write(text)


__all__ = ["ensure_spill_dir", "open_exclusive", "write_text_exclusive"]
