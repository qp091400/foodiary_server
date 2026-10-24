"""Start Cloud Run after checking durable uploads and production configuration."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.services.ocr.manifest import verify_model_files


def check_runtime() -> None:
    if settings.app_env != "production" or settings.rate_limit_scope != "global":
        raise RuntimeError("Cloud Run requires production mode and global request limiting")
    settings.validate_for_startup()
    if not settings.ocr_models_dir:
        raise RuntimeError("Cloud Run requires baked OCR models")
    verify_model_files()
    root = Path(settings.upload_dir)
    # Prevent an accidentally omitted volume from silently losing receipts on restart.
    if not root.is_mount():
        raise RuntimeError("UPLOAD_DIR must be a persistent Cloud Storage volume mount")
    with tempfile.NamedTemporaryFile(dir=root, prefix=".storage-check-") as probe:
        probe.write(b"foodiary-storage-check")
        probe.flush()
        os.fsync(probe.fileno())
    port = int(os.environ.get("PORT", "8080"))
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT must be between 1 and 65535")


def main() -> None:
    check_runtime()
    # Migrations run as a separate one-task job before deploying this service.
    # Forwarded headers are deliberately ignored. The instance-wide limiter cannot
    # be bypassed by supplying arbitrary X-Forwarded-For values.
    os.execvp("uvicorn", [
        "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", os.environ.get("PORT", "8080"),
        "--workers", "1", "--no-proxy-headers", "--limit-concurrency", "32",
        "--timeout-keep-alive", "5", "--no-server-header",
    ])


if __name__ == "__main__":
    main()
