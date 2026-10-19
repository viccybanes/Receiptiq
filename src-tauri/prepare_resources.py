"""Refresh only generated frontend resources before Tauri copies the complete backend bundle."""

import shutil
import tempfile
from pathlib import Path

project = Path(__file__).resolve().parents[1]
source = (project / "frontend/dist").resolve()
bundle = (project / "dist/receiptiq").resolve()
target = (bundle / "_internal/frontend/dist").resolve()
if not source.is_relative_to(project) or not bundle.is_relative_to(project) or not target.is_relative_to(bundle):
    raise RuntimeError("Build resources must stay inside this project.")
if not (source / "index.html").is_file() or not (bundle / "receiptiq.exe").is_file():
    raise RuntimeError("Build the frontend and portable backend before building the desktop.")
target.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="frontend-prepare-", dir=target.parent) as temporary:
    prepared = Path(temporary) / "dist"
    shutil.copytree(source, prepared)
    if target.exists():
        shutil.rmtree(target)
    prepared.replace(target)
