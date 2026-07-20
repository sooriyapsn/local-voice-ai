"""Parent-controlled settings: session time limit and an optional custom
story/lesson the agent should use, persisted to the same volume as model
weights so it survives container restarts. Gated by a PIN (env-configured,
not user-changeable in this version) rather than real auth — this app runs
on a home LAN for one family, not a multi-tenant service.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("parent_settings")

STORY_TEXT_MAX_CHARS = 6000


def _settings_path() -> Path:
    # Read lazily (not at import time) so tests can monkeypatch the env var
    # per-case instead of sharing one real path across the whole suite.
    return Path(os.getenv("PARENT_SETTINGS_PATH", "/models/parent-settings.json"))


@dataclass
class ParentSettings:
    time_limit_minutes: int = 30
    story_title: str = ""
    story_text: str = ""


def verify_pin(pin: str) -> bool:
    return hmac.compare_digest(pin, os.getenv("PARENT_PIN") or "1234")


def load_settings() -> ParentSettings:
    path = _settings_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text())
            fields = ParentSettings.__dataclass_fields__
            return ParentSettings(**{k: raw[k] for k in fields if k in raw})
        except (json.JSONDecodeError, OSError, TypeError):
            logger.warning("could not read parent settings, using defaults")
    return ParentSettings()


def save_settings(settings: ParentSettings) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings)))
