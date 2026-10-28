"""Small workspace preferences, retained by existing backups and relocation."""

import json
from typing import Literal

from filelock import FileLock
from pydantic import Field, StrictBool

from .google_key import KEY_FILE
from .models import StrictModel
from .system import _write_json


class VisionSettings(StrictModel):
    amount_first: StrictBool = False
    capture_regions: StrictBool = False
    provider: Literal["local", "google"] = "local"
    google_model: str = Field(default="gemini-3.8-flash", max_length=100, pattern=r"^gemini-[a-zA-Z0-9._-]+$")
    google_consent: StrictBool = False


def load_vision_settings(root):
    path = root / "settings.json"
    values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return VisionSettings.model_validate(values.get("vision", {}))


def public_vision_settings(root):
    return {**load_vision_settings(root).model_dump(), "google_key_saved": (root / KEY_FILE).is_file()}


def selected_vision(root, model=None, *, provider=None, google_model=None):
    """Pin a non-secret provider/model selection before work enters the durable queue."""
    settings = load_vision_settings(root)
    if (provider or settings.provider) == "google":
        return {"provider": "google", "model": google_model or settings.google_model}
    if model is None:
        from .vision import ALLOWED_MODELS, DEFAULT_MODEL

        model = DEFAULT_MODEL
        try:
            profile = json.loads((root / "setup-profile.json").read_text(encoding="utf-8"))
            if profile.get("model") in ALLOWED_MODELS:
                model = profile["model"]
        except (OSError, ValueError):
            pass
    return {"provider": "local", "model": model}


def require_google_enabled(root):
    settings = load_vision_settings(root)
    if settings.provider != "google" or not settings.google_consent or not (root / KEY_FILE).is_file():
        raise ValueError("Google vision is disabled. Select Google, save a key, and enable cloud processing in Settings.")


def save_vision_settings(root, changes, *, clear_key=False):
    with FileLock(str(root / "settings.lock"), timeout=5):
        path = root / "settings.json"
        values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        current = load_vision_settings(root).model_dump()
        current.update(changes.model_dump(exclude_unset=True))
        settings = VisionSettings.model_validate(current)
        if settings.provider == "google" and (not settings.google_consent or not (root / KEY_FILE).is_file()):
            raise ValueError("Save a Google API key and explicitly allow cloud processing before selecting Google.")
        if settings.provider == "local":
            settings.google_consent = False
        values["vision"] = settings.model_dump()
        _write_json(path, values)
        if clear_key:
            (root / KEY_FILE).unlink(missing_ok=True)
        return public_vision_settings(root)
