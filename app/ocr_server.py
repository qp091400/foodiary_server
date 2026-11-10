"""OCR server: reads receipt photos for the API server. No database, no users.

POST /read takes one photo and returns the OCR boxes. Parsing and name fixes stay in the API (same code).
It is private: on Cloud Run only the API service's account may invoke it (IAM); locally it is not published.
Run: uvicorn app.ocr_server:app --port 8001 (with OCR_ENGINE=paddle).
"""

import logging
import tempfile
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image

from app.config import settings
from app.routers.receipts import read_receipt
from app.services.ocr import get_ocr_engine
from app.services.ocr.base import OcrBusyError

logger = logging.getLogger(__name__)
MAX_PHOTO_BYTES = 20 * 1024 * 1024  # the API already checked and re-encoded the photo; this only bounds memory


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.ocr_warmup:
        from app.services.ocr.paddle import get_paddle_engine

        get_paddle_engine().warmup()
    yield


app = FastAPI(title="Foodiary OCR", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/ready")
def ready():
    if settings.ocr_warmup:
        from app.services.ocr.paddle import get_paddle_engine

        if not get_paddle_engine().ready:
            return JSONResponse(status_code=503, content={"ocr": "unavailable"})
    return {"ocr": "ready"}


@app.post("/read")
def read(file: UploadFile = File(), wait: float = Form(0, ge=0, le=600), ticket: int | None = Form(None)):
    data = file.file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="photo too large")
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as photo:
        photo.write(data)
    path = Path(photo.name)
    try:
        with Image.open(path) as image:
            landscape = image.width > image.height
        boxes = read_receipt(get_ocr_engine(), str(path), landscape=landscape, wait=wait, ticket=ticket)
    except OcrBusyError:
        raise HTTPException(status_code=503, detail="busy", headers={"Retry-After": "10"}) from None
    finally:
        path.unlink(missing_ok=True)
    return {"boxes": [asdict(box) for box in boxes]}
