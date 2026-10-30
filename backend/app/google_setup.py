"""One-time guide state and an image-based key test that never opens a receipt."""

import base64
import io
import json
from typing import Literal

from filelock import FileLock
from PIL import Image, ImageDraw, ImageFont

from .extraction import decimal
from .google_key import load_google_key, validate_google_key
from .google_vision import GoogleVisionEngine
from .models import StrictModel
from .system import _write_json
from .vision import SYSTEM_PROMPT, VisionError, _output_schema
from .vision_settings import require_google_enabled

TEST_MODEL = "gemini-3.5-flash-lite"


class GoogleSetupInput(StrictModel):
    status: Literal["completed", "skipped"]


def google_setup_status(root):
    path = root / "settings.json"
    values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {"status": values.get("gemini_setup", {}).get("status", "pending")}


def save_google_setup(root, status):
    with FileLock(str(root / "settings.lock"), timeout=5):
        if status == "completed":
            require_google_enabled(root)
        path = root / "settings.json"
        values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        values["gemini_setup"] = {"status": status}
        _write_json(path, values)
    return {"status": status}


def test_google_connection(root, api_key):
    """One bounded request; no key persistence, jobs, database writes, or retrieval."""
    key = validate_google_key(api_key) if api_key is not None else load_google_key(root)
    image = Image.new("RGB", (720, 420), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=26)
    lines = ["RECEIPTOR SAMPLE", "Receipt: TEST-001", "Date: 2026-01-01",
             "Test item   2 pcs x PHP 10.00", "Amount: PHP 20.00", "TOTAL: PHP 20.00"]
    for index, line in enumerate(lines):
        draw.text((32, 28 + index * 55), line, fill="black", font=font)
    output = io.BytesIO()
    image.save(output, "JPEG", quality=90)
    payload = {"model": TEST_MODEL, "format": _output_schema(), "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Read this sample receipt image. Return the printed fields and item.",
         "images": [base64.b64encode(output.getvalue()).decode("ascii")]},
    ]}
    receipt, incomplete = GoogleVisionEngine(root, setup_key=key)._read_receipt(payload, None)
    try:
        valid = (not incomplete and len(receipt.items) == 1 and decimal(receipt.total) == 20 and
                 decimal(receipt.items[0].quantity) == 2 and decimal(receipt.items[0].unit_price) == 10 and
                 decimal(receipt.items[0].line_total) == 20)
    except ValueError:
        valid = False
    if not valid:
        raise VisionError("Google responded, but the sample receipt was not read correctly. Retry the test.")
    return {"ok": True, "model": TEST_MODEL}
