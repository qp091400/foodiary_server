"""Start the private OCR service on Cloud Run after checking the baked models. No database, no uploads volume."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.services.ocr.manifest import verify_model_files


def check_runtime() -> None:
    if settings.ocr_engine.strip().lower() != "paddle" or not settings.ocr_warmup:
        raise RuntimeError("The OCR service requires OCR_ENGINE=paddle and OCR_WARMUP=true")
    if not settings.ocr_models_dir:
        raise RuntimeError("The OCR service requires baked OCR models")
    verify_model_files()


def main() -> None:
    check_runtime()
    os.execvp("uvicorn", [
        "uvicorn", "app.ocr_server:app", "--host", "0.0.0.0", "--port", os.environ.get("PORT", "8080"),
        "--workers", "1", "--no-proxy-headers", "--limit-concurrency", "32",
        "--timeout-keep-alive", "5", "--no-server-header",
    ])


if __name__ == "__main__":
    main()
