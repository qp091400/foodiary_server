"""OCR_ENGINE=remote: the API server sends each photo to the OCR server (app/ocr_server.py) and gets boxes back.

The OCR server rereads doubtful photos itself (same parser, same judge) and queues photos in request-ticket order,
so this engine reports can_reread / can_queue and ignores the judge and rank callbacks.
"""

import threading
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.services.ocr.base import OcrBox, OcrBusyError

METADATA_TOKEN_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
TOKEN_SECONDS = 45 * 60  # Google ID tokens live one hour
READY_SECONDS = 30  # a good readiness answer is reused this long; /ready is public and must not hammer the OCR service


class RemoteOcrEngine:
    can_reread = True
    can_queue = True

    def __init__(self, base_url: str, *, auth: str = "none", engines: int = 1, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.engines = engines  # receipts.run_batch sends up to 2 x engines photos ahead
        self._client = client or httpx.Client()
        self._token: tuple[str, float] | None = None
        self._token_lock = threading.Lock()
        self._probe_lock = threading.Lock()
        self._ready_until = 0.0
        self._last_ready = False

    def _headers(self) -> dict[str, str]:
        if self.auth != "google":
            return {}
        with self._token_lock:
            if self._token is None or self._token[1] < time.monotonic():
                # Cloud Run's metadata server signs an ID token for this service's account; the OCR service's IAM checks it.
                response = self._client.get(
                    METADATA_TOKEN_URL, params={"audience": self.base_url}, headers={"Metadata-Flavor": "Google"}, timeout=5
                )
                response.raise_for_status()
                self._token = (response.text.strip(), time.monotonic() + TOKEN_SECONDS)
            return {"Authorization": f"Bearer {self._token[0]}"}

    def recognize(self, image_path: str, *, judge: Any = None, rank: Any = None, wait: float = 0, ticket: int | None = None) -> list[OcrBox]:
        with open(image_path, "rb") as image:
            response = self._client.post(
                f"{self.base_url}/read",
                headers=self._headers(),
                files={"file": (Path(image_path).name, image, "image/jpeg")},
                # shortcut: tickets count per API process, so right after an API restart new requests can jump the OCR queue
                # for a moment; fine with one API instance, give the OCR server its own ticket source if the API scales out
                data={"wait": str(wait), **({"ticket": str(ticket)} if ticket is not None else {})},
                timeout=wait + settings.ocr_remote_timeout_seconds,
            )
        if response.status_code == 503:
            raise OcrBusyError("OCR server busy")
        response.raise_for_status()
        return [OcrBox(**box) for box in response.json()["boxes"]]

    def ready(self) -> bool:
        """Is the OCR service up? One probe at a time (it may wait ~40 s for a cold start); callers meanwhile get the last answer."""
        if time.monotonic() < self._ready_until:
            return True
        if not self._probe_lock.acquire(blocking=False):
            return self._last_ready
        try:
            try:
                ok = self._client.get(f"{self.base_url}/ready", headers=self._headers(), timeout=60).status_code == 200
            except httpx.HTTPError:
                ok = False
            self._last_ready = ok
            if ok:
                self._ready_until = time.monotonic() + READY_SECONDS
            return ok
        finally:
            self._probe_lock.release()


_engine: RemoteOcrEngine | None = None
_engine_lock = threading.Lock()


def get_remote_engine() -> RemoteOcrEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = RemoteOcrEngine(settings.ocr_service_url, auth=settings.ocr_service_auth, engines=settings.ocr_remote_engines)
    return _engine
