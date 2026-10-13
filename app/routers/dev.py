"""개발용 도구. DEV_TOOLS=true일 때만 열린다.

로그인 없이 OCR을 돌리는 경로라서 운영에서는 반드시 끈다. 꺼져 있으면 없는 경로처럼 404를 준다.
"""

import os
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.routers.receipts import build_draft, draft_warnings, load_image, plausible_purchase_date, read_upload
from app.services import barcode as barcode_service
from app.services.ocr import OcrEngine, get_ocr_engine
from app.services.receipt_parser import parse_receipt
from app.timeutil import today

PAGE_PATH = Path(__file__).resolve().parent.parent / "static" / "receipt_test.html"


def require_dev_tools() -> None:
    if not settings.dev_tools:
        raise HTTPException(status_code=404, detail="요청한 경로를 찾을 수 없습니다.")


router = APIRouter(prefix="/dev", include_in_schema=False, dependencies=[Depends(require_dev_tools)])


@router.get("/receipt-test", response_class=HTMLResponse)
def receipt_test_page():
    return HTMLResponse(PAGE_PATH.read_text(encoding="utf-8"))


@router.post("/receipt-test")
def receipt_test(
    file: UploadFile = File(),
    db: Session = Depends(get_db),
    ocr: OcrEngine = Depends(get_ocr_engine),
):
    """영수증 업로드와 같은 순서로 인식만 해 본다. 영수증도 이미지도 저장하지 않는다."""
    image = load_image(read_upload(file))
    handle, path = tempfile.mkstemp(suffix=".jpg")
    os.close(handle)
    ocr_error = None
    try:
        image.save(path, "JPEG", quality=90)
        started = time.perf_counter()
        try:
            boxes = ocr.recognize(path)
        except Exception as exc:
            # 실제 업로드는 이 경우 빈 초안으로 넘어간다. 여기서는 원인을 보여 준다.
            boxes, ocr_error = [], f"{type(exc).__name__}: {exc}"
        ocr_seconds = round(time.perf_counter() - started, 1)
    finally:
        Path(path).unlink(missing_ok=True)

    parsed = parse_receipt(boxes)
    purchased_on = plausible_purchase_date(parsed.purchased_on)
    draft_items = build_draft(db, parsed, purchased_on or today())
    items_sum = sum(item["amount"] - item["discount"] for item in draft_items)
    return {
        "store_name": parsed.store_name,
        "purchased_on": purchased_on,
        "total_amount": parsed.total_amount,
        "receipt_barcode": barcode_service.decode_receipt_barcode(image),
        "draft_items": draft_items,
        "items_sum": items_sum,
        "warnings": draft_warnings(len(draft_items), items_sum, parsed.total_amount),
        "raw_lines": parsed.lines,
        "ocr_seconds": ocr_seconds,
        "ocr_error": ocr_error,
    }
