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
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.routers.receipts import (
    OCR_BUSY,
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


def _recognize(file: UploadFile, db: Session, ocr: OcrEngine, *, show_errors: bool) -> dict:
    received = time.perf_counter()  # 사진을 다 받은 뒤부터. 페이지가 전체 시간과 비교해 업로드·대기 시간을 가늠한다
    image = load_image(read_upload(file))
    handle, path = tempfile.mkstemp(suffix=".jpg")
    os.close(handle)
    ocr_error = None
    try:
        image.save(path, "JPEG", quality=90)
        started = time.perf_counter()
        try:
            boxes = read_receipt(ocr, path, landscape=image.width > image.height)
        except OcrBusyError:
            raise HTTPException(status_code=503, detail=OCR_BUSY, headers={"Retry-After": "10"}) from None
        except Exception as exc:
            # 실제 업로드는 이 경우 빈 초안으로 넘어간다. 개발 페이지에서는 원인을 보여 준다.
            logger.exception("영수증 인식 테스트 OCR 실패")
            boxes, ocr_error = [], f"{type(exc).__name__}: {exc}" if show_errors else "영수증 인식에 실패했습니다."
        ocr_seconds = round(time.perf_counter() - started, 1)
    finally:
        Path(path).unlink(missing_ok=True)

    parsed = parse_receipt(boxes)
    purchased_on = plausible_purchase_date(parsed.purchased_on)
    draft_items = build_draft(db, parsed, purchased_on or today())
    items_sum = sum(item["amount"] - item["discount"] for item in draft_items)
    receipt_barcode = barcode_service.decode_receipt_barcode(image)
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
        "ocr_seconds": ocr_seconds,
        "server_seconds": round(time.perf_counter() - received, 1),
        "ocr_error": ocr_error,
    }


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
    return _recognize(file, db, ocr, show_errors=True)


def _recognize_many(request: Request, files: list[UploadFile], db: Session, ocr: OcrEngine, *, show_errors: bool) -> dict:
    """여러 장 업로드와 같은 순서(한 장씩, 한 장이 실패해도 계속)로 인식만 해 본다. 아무것도 저장하지 않는다."""
    outcomes = run_batch(request, files, lambda file: _recognize(file, db, ocr, show_errors=show_errors), db=db)
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
    return _recognize(file, db, ocr, show_errors=False)
