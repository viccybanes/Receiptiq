"""Small workspace preferences, retained by existing backups and relocation."""

import json

from filelock import FileLock
from pydantic import StrictBool

from .models import StrictModel
from .system import _write_json


class VisionSettings(StrictModel):
    amount_first: StrictBool = False
    capture_regions: StrictBool = False


def load_vision_settings(root):
    path = root / "settings.json"
    values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return VisionSettings.model_validate(values.get("vision", {}))


def save_vision_settings(root, changes):
    with FileLock(str(root / "settings.lock"), timeout=5):
        path = root / "settings.json"
        values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        current = load_vision_settings(root).model_dump()
        current.update(changes.model_dump(exclude_unset=True))
        values["vision"] = VisionSettings.model_validate(current).model_dump()
        _write_json(path, values)
        return values["vision"]
