"""Process configuration from environment variables (see deploy/time-assistant.env.example).

User-editable preferences (timezone, search hours, reminder defaults, AI
providers, backups) live in the database `settings` table instead, so they can
be changed from the web UI.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    data_dir: Path
    db_path: Path
    backup_dir: Path
    run_background: bool  # reminder + backup loops (disabled in tests)


def load() -> Config:
    data_dir = Path(os.environ.get("TA_DATA_DIR", BASE_DIR / "data"))
    return Config(
        data_dir=data_dir,
        db_path=Path(os.environ.get("TA_DB_PATH", data_dir / "time_assistant.db")),
        backup_dir=Path(os.environ.get("TA_BACKUP_DIR", data_dir / "backups")),
        run_background=os.environ.get("TA_BACKGROUND", "1") == "1",
    )
