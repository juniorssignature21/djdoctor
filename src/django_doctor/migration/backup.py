"""SQLite safety backups before destructive migrations.

Uses SQLite's online backup API so the copy is consistent even if another
process has the database open. Other databases are not backed up
automatically: Django Doctor tells the user to take a backup instead.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from django_doctor.project import DjangoProject, make_private


def sqlite_database_path(project: DjangoProject, info: dict[str, Any] | None, alias: str = "default") -> Path | None:
    db = ((info or {}).get("databases") or {}).get(alias) or {}
    if not (db.get("engine") or "").endswith("sqlite3"):
        return None
    name = db.get("name") or ""
    if not name or name == ":memory:" or name.startswith("file:"):
        return None
    path = Path(name)
    if not path.is_absolute():
        path = project.root / path
    return path if path.is_file() else None


def backup_sqlite(project: DjangoProject, db_path: Path) -> Path:
    backups = project.ensure_state_dir() / "backups"
    backups.mkdir(mode=0o700, exist_ok=True)
    make_private(backups, directory=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backups / f"{db_path.stem}-{stamp}{db_path.suffix or '.sqlite3'}"
    counter = 1
    while target.exists():
        target = backups / f"{db_path.stem}-{stamp}-{counter}{db_path.suffix or '.sqlite3'}"
        counter += 1
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        # Create the file private *before* copying data into it.
        os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        dest = sqlite3.connect(target)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    make_private(target)
    return target
