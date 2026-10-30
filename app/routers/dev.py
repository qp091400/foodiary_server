"""영수증 인식 테스트 페이지. 업로드와 같은 순서로 인식만 하고 아무것도 저장하지 않는다.

- /dev/receipt-test: DEV_TOOLS=true일 때만 열린다. 로그인 없이 OCR을 돌리는 경로라서 운영에서는 반드시 끈다.
- /receipt-test: RECEIPT_TEST_PASSWORD를 설정했을 때만 열린다. 인식 요청마다 X-Test-Password 헤더로
  그 비밀번호를 보내야 한다. 운영 서버에서 쓰는 쪽이라 OCR 오류의 자세한 내용은 보여 주지 않는다.

꺼져 있으면 없는 경로처럼 404를 준다.
"""

import logging
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.routers.receipts import (
    OCR_BUSY,
    BatchSteps,
    build_draft,
    draft_warnings,
    load_image,
    plausible_purchase_date,
    read_receipt,
    read_upload,
    run_batch,
)
from app.services import barcode as barcode_service
from app.services.ocr import OcrEngine, get_ocr_engine
from app.services.ocr.base import OcrBusyError
from app.services.receipt_parser import parse_receipt
from app.timeutil import today

logger = logging.getLogger(__name__)

PAGE_PATH = Path(__file__).resolve().parent.parent / "static" / "receipt_test.html"
NOT_FOUND = "요청한 경로를 찾을 수 없습니다."


def require_dev_tools() -> None:
    if not settings.dev_tools:
        raise HTTPException(status_code=404, detail=NOT_FOUND)


def require_test_page() -> None:
    if not settings.receipt_test_password:
        raise HTTPException(status_code=404, detail=NOT_FOUND)


def require_test_password(x_test_password: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_test_password.encode(), settings.receipt_test_password.encode()):
        raise HTTPException(status_code=401, detail="테스트 비밀번호가 맞지 않습니다.")


router = APIRouter(prefix="/dev", include_in_schema=False, dependencies=[Depends(require_dev_tools)])
test_router = APIRouter(prefix="/receipt-test", include_in_schema=False, dependencies=[Depends(require_test_page)])


def _page() -> HTMLResponse:
    return HTMLResponse(PAGE_PATH.read_text(encoding="utf-8"))


@dataclass
class _TestPhoto:
    """OCR을 기다리는 사진 한 장. 임시 파일로만 두고 아무것도 저장하지 않는다."""

    image: Any
    path: str
    received: float  # 사진을 다 받은 때. 페이지가 전체 시간과 비교해 업로드·대기 시간을 가늠한다
    ocr_seconds: float = 0.0
    ocr_error: str | None = None

    def discard(self) -> None:
        Path(self.path).unlink(missing_ok=True)


def _prepare_test_photo(file: UploadFile) -> _TestPhoto:
    received = time.perf_counter()
    image = load_image(read_upload(file))
    handle, path = tempfile.mkstemp(suffix=".jpg")
    os.close(handle)
    photo = _TestPhoto(image, path, received)
    try:
        image.save(path, "JPEG", quality=90)
    except Exception:
        photo.discard()
        raise
    return photo


def _read_test_photo(photo: _TestPhoto, ocr: OcrEngine, *, show_errors: bool, wait: float = 0, ticket: int | None = None):
    """OCR만 한다. DB를 쓰지 않아서 여러 스레드에서 동시에 불러도 된다. 읽고 나면 임시 파일을 지운다."""
    started = time.perf_counter()
    boxes: list = []
    try:
        boxes = read_receipt(ocr, photo.path, landscape=photo.image.width > photo.image.height, wait=wait, ticket=ticket)
    except OcrBusyError:
        raise HTTPException(status_code=503, detail=OCR_BUSY, headers={"Retry-After": "10"}) from None
    except Exception as exc:
        # 실제 업로드는 이 경우 빈 초안으로 넘어간다. 개발 페이지에서는 원인을 보여 준다.
        logger.exception("영수증 인식 테스트 OCR 실패")
        photo.ocr_error = f"{type(exc).__name__}: {exc}" if show_errors else "영수증 인식에 실패했습니다."
    finally:
        photo.ocr_seconds = round(time.perf_counter() - started, 1)
        photo.discard()
    return boxes


def _finish_test_photo(photo: _TestPhoto, boxes: list, db: Session) -> dict:
    parsed = parse_receipt(boxes)
    purchased_on = plausible_purchase_date(parsed.purchased_on)
    draft_items = build_draft(db, parsed, purchased_on or today())
    items_sum = sum(item["amount"] - item["discount"] for item in draft_items)
    receipt_barcode = barcode_service.decode_receipt_barcode(photo.image)
    return {
        "store_name": parsed.store_name,
        "purchased_on": purchased_on,
        "total_amount": parsed.total_amount,
        "receipt_barcode": receipt_barcode,
        "draft_items": draft_items,
        "items_sum": items_sum,
        "warnings": draft_warnings(
            len(draft_items), items_sum, parsed.total_amount, name_cut=any(item["name_cut"] for item in draft_items)
        ),
        "raw_lines": parsed.lines,
        "ocr_seconds": photo.ocr_seconds,
        "server_seconds": round(time.perf_counter() - photo.received, 1),
        "ocr_error": photo.ocr_error,
    }


def _recognize(file: UploadFile, db: Session, ocr: OcrEngine, *, show_errors: bool, wait: float = 0) -> dict:
    photo = _prepare_test_photo(file)
    return _finish_test_photo(photo, _read_test_photo(photo, ocr, show_errors=show_errors, wait=wait), db)


@router.get("/receipt-test", response_class=HTMLResponse)
def receipt_test_page():
    return _page()


@router.post("/receipt-test")
def receipt_test(
    file: UploadFile = File(),
    db: Session = Depends(get_db),
    ocr: OcrEngine = Depends(get_ocr_engine),
):
    """로그인 없이 인식만 해 본다. 영수증도 이미지도 저장하지 않는다."""
    return _recognize(file, db, ocr, show_errors=True, wait=settings.ocr_queue_wait_seconds)


def _recognize_many(request: Request, files: list[UploadFile], db: Session, ocr: OcrEngine, *, show_errors: bool) -> dict:
    """여러 장 업로드와 같은 방식(한 장이 실패해도 계속, 엔진이 여러 개면 동시에)으로 인식만 해 본다. 아무것도 저장하지 않는다."""
    steps = BatchSteps(
        prepare=_prepare_test_photo,
        read=lambda photo, wait, ticket: _read_test_photo(photo, ocr, show_errors=show_errors, wait=wait, ticket=ticket),
        finish=lambda photo, boxes: _finish_test_photo(photo, boxes, db),
    )
    outcomes = run_batch(request, files, steps, ocr=ocr, db=db)
    return {
        "results": [
            {
                "index": o.index, "filename": o.filename, "ok": o.ok, "status_code": o.status_code,
                "result": o.payload, "detail": o.detail, "retry_after": o.retry_after,
            }
            for o in outcomes
        ]
    }


@router.post("/receipt-test/batch")
def receipt_test_batch(
    request: Request,
    files: list[UploadFile] = File(),
    db: Session = Depends(get_db),
    ocr: OcrEngine = Depends(get_ocr_engine),
):
    """사진 여러 장을 로그인 없이 인식만 해 본다."""
    return _recognize_many(request, files, db, ocr, show_errors=True)


@test_router.get("", response_class=HTMLResponse)
def password_receipt_test_page():
    return _page()


@test_router.post("/batch", dependencies=[Depends(require_test_password)])
def password_receipt_test_batch(
    request: Request,
    files: list[UploadFile] = File(),
    db: Session = Depends(get_db),
    ocr: OcrEngine = Depends(get_ocr_engine),
):
    """테스트 비밀번호로 사진 여러 장을 인식만 해 본다."""
    return _recognize_many(request, files, db, ocr, show_errors=False)


@test_router.post("", dependencies=[Depends(require_test_password)])
def password_receipt_test(
    file: UploadFile = File(),
    db: Session = Depends(get_db),
    ocr: OcrEngine = Depends(get_ocr_engine),
):
    """테스트 비밀번호로 인식만 해 본다. 영수증도 이미지도 저장하지 않는다."""
    return _recognize(file, db, ocr, show_errors=False, wait=settings.ocr_queue_wait_seconds)
