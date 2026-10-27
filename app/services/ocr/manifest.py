"""Compare downloaded model files with the revision tested for deployment."""

import hashlib
import json
import os
from pathlib import Path

from app.config import settings


def verify_model_files(*, cache: Path | None = None, manifest: Path | None = None) -> dict[str, str]:
    cache = cache or (
        Path(settings.ocr_models_dir) if settings.ocr_models_dir else
        Path(os.environ.get("PADDLE_PDX_CACHE_HOME", str(Path.home() / ".paddlex"))) / "official_models"
    )
    manifest = manifest or Path(__file__).resolve().parents[3] / "deploy" / "model-manifest.json"
    models = {settings.ocr_detection_model, settings.ocr_recognition_model, settings.ocr_textline_model}
    if settings.ocr_fast_detection_model:
        models.add(settings.ocr_fast_detection_model)
    # Only the configured models are checked, so the fast model can be switched off without editing the manifest.
    expected = {
        name: value
        for name, value in json.loads(manifest.read_text(encoding="utf-8"))["sha256"].items()
        if name.split("/", 1)[0] in models
    }
    if {name.split("/", 1)[0] for name in expected} != models:
        raise RuntimeError("OCR model names differ from the verified deployment manifest")
    actual = {}
    for name, expected_hash in expected.items():
        path = cache / name
        if not path.is_file():
            raise RuntimeError(f"Verified OCR model file is missing: {name}")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        actual[name] = digest.hexdigest()
        if actual[name] != expected_hash:
            raise RuntimeError(f"OCR model checksum differs from the verified revision: {name}")
    return actual
