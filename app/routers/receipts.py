"""영수증 업로드(초안) → 확정. OCR은 100% 정확하지 않으므로 반드시 2단계를 거친다."""

import io
import logging
import math
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import FridgeAccess, get_fridge_access
from app.models import FoodItem, Receipt, ReceiptItem
from app.schemas import (
    ConfirmItem,
    DraftItem,
    ItemOut,
    ReceiptConfirmRequest,
    ReceiptConfirmResponse,
    ReceiptDetail,
    ReceiptItemOut,
    ReceiptSummary,
)
from app.services import barcode as barcode_service
from app.services.expiry import classify, estimate
from app.services.foodsafety import ProductInfo, lookup_barcode
from app.services.ocr import OcrEngine, get_ocr_engine
from app.services.ocr.base import OcrBox, OcrBusyError
from app.services.receipt_parser import ParsedReceipt, parse_receipt
from app.timeutil import today, utcnow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/fridges/{fridge_id}/receipts", tags=["영수증"])

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP"}  # MPO: 일부 폰 카메라가 저장하는 JPEG 변형
MAX_SIDE = 2000
DUPLICATE_RECEIPT = "이미 등록된 영수증입니다."
WARN_SUM_MISMATCH = "일부 품목을 못 읽었을 수 있어요"
WARN_NO_ITEMS = "품목을 찾지 못했어요. 직접 추가해 주세요"
WARN_NO_TOTAL = "영수증 총액을 찾지 못했어요. 빠진 품목이 없는지 확인해 주세요"
OCR_BUSY = "다른 영수증을 분석 중입니다. 잠시 후 다시 시도해 주세요."
# 빠른 결과가 맞지 않아 정밀 모델로 다시 읽을 때, 그 사이 들어온 다른 영수증이 끝나길 기다리는 최대 시간(초)
FULL_PASS_WAIT_SECONDS = 60  # TODO: balance


class _ProductLookup:
    """한 요청 안에서 상품 바코드를 조회한다. 한 번 실패하면(네트워크 장애 등) 나머지는 건너뛴다."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._failed = False

    def get(self, code: str | None) -> ProductInfo | None:
        if not code or self._failed:
            return None
        result = lookup_barcode(self._db, code)
        self._failed = result.source == "error"
        return result.product


def read_upload(file: UploadFile) -> bytes:
    """확장자와 크기를 검사하고 파일 내용을 돌려준다."""
    if Path(file.filename or "").suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="jpg, png, webp 이미지만 올릴 수 있습니다.")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"파일이 너무 큽니다. (최대 {settings.max_upload_mb}MB)")
    if not data:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    return data


def load_image(data: bytes) -> Image.Image:
    """폰 사진 회전 보정(EXIF) → RGB → 긴 변 2000px."""
    try:
        image = Image.open(io.BytesIO(data))
        if image.format not in ALLOWED_FORMATS:
            raise ValueError(f"unsupported format: {image.format}")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        return image
    except Exception:
        raise HTTPException(status_code=400, detail="이미지를 열 수 없습니다. jpg, png, webp 파일인지 확인해 주세요.") from None


def _remove_file(relative_path: str | None) -> None:
    if relative_path:
        (Path(settings.upload_dir) / relative_path).unlink(missing_ok=True)


def build_draft(db: Session, parsed: ParsedReceipt, purchased_on: date) -> list[dict]:
    lookup = _ProductLookup(db)
    draft = []
    for item in parsed.items:
        product = lookup.get(item.barcode)
        guess = estimate(item.name, purchased_on, product)
        draft.append(
            DraftItem(
                line_no=item.line_no,
                name=item.name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                amount=item.amount,
                discount=item.discount,
                barcode=item.barcode,
                category=guess.category,
                category_label=guess.category_label,
                storage=guess.storage,
                is_food=guess.is_food,
                include=guess.is_food,
                expiry_date=guess.expiry_date,
                expiry_source=guess.expiry_source,
                product_name=product.name if product else None,
                confidence=item.confidence,
            ).model_dump(mode="json")
        )
    return draft


def plausible_purchase_date(purchased_on: date | None) -> date | None:
    """미래 날짜는 오인식으로 보고 버린다."""
    return purchased_on if purchased_on and purchased_on <= today() else None


def checks_out(parsed: ParsedReceipt) -> bool:
    """품목이 있고, 총액을 읽었고, 품목 합계(금액 - 할인)가 총액과 맞는다. 곧 초안에 경고가 없다."""
    items_sum = sum(item.amount - item.discount for item in parsed.items)
    return not draft_warnings(len(parsed.items), items_sum, parsed.total_amount)


def read_receipt(ocr: OcrEngine, image_path: str) -> list[OcrBox]:
    """빠른 모델이 있으면 먼저 읽고, 결과가 맞아떨어지면 그대로 쓴다. 아니면 정밀 모델로 다시 읽는다."""
    if not getattr(ocr, "has_fast_model", False):
        return ocr.recognize(image_path)
    started = time.perf_counter()
    try:
        boxes = ocr.recognize(image_path, fast=True)
        if checks_out(parse_receipt(boxes)):
            logger.info("영수증 OCR: 빠른 모델로 끝남 (%.1f초)", time.perf_counter() - started)
            return boxes
    except OcrBusyError:
        raise
    except Exception:
        logger.exception("빠른 모델 OCR 실패. 정밀 모델로 다시 읽는다")
    fast_seconds = time.perf_counter() - started
    boxes = ocr.recognize(image_path, wait=FULL_PASS_WAIT_SECONDS)
    logger.info(
        "영수증 OCR: 빠른 모델 결과가 맞지 않아 정밀 모델로 다시 읽음 (빠른 %.1f초 + 정밀 %.1f초)",
        fast_seconds,
        time.perf_counter() - started - fast_seconds,
    )
    return boxes


def draft_warnings(item_count: int, items_sum: int, total_amount: int | None) -> list[str]:
    if item_count == 0:
        return [WARN_NO_ITEMS]
    if not total_amount:  # 합계를 못 읽으면 빠진 품목이 있어도 알 수 없다
        return [WARN_NO_TOTAL]
    if abs(items_sum - total_amount) > total_amount * 0.01:
        return [WARN_SUM_MISMATCH]
    return []


def _detail(db: Session, receipt: Receipt) -> ReceiptDetail:
    draft_items = [DraftItem.model_validate(item) for item in receipt.draft_items or []]
    items = db.scalars(
        select(ReceiptItem).where(ReceiptItem.receipt_id == receipt.id).order_by(ReceiptItem.line_no)
    ).all()
    if receipt.status == "pending":
        items_sum = sum(item.amount - item.discount for item in draft_items)
        count = len(draft_items)
    else:
        items_sum = sum(item.amount - item.discount for item in items)
        count = len(items)

    return ReceiptDetail(
        id=receipt.id,
        fridge_id=receipt.fridge_id,
        status=receipt.status,
        store_name=receipt.store_name,
        purchased_on=receipt.purchased_on,
        total_amount=receipt.total_amount,
        receipt_barcode=receipt.receipt_barcode,
        uploaded_by=receipt.uploaded_by,
        created_at=receipt.created_at,
        confirmed_at=receipt.confirmed_at,
        draft_items=draft_items,
        items=[ReceiptItemOut.model_validate(item) for item in items],
        items_sum=items_sum,
        warnings=draft_warnings(count, items_sum, receipt.total_amount),
        raw_lines=receipt.raw_lines or [],
    )


def _get_receipt(db: Session, fridge_id: int, receipt_id: int, *, lock: bool = False) -> Receipt:
    receipt = db.get(Receipt, receipt_id, with_for_update=lock)
    if receipt is None or receipt.fridge_id != fridge_id:
        raise HTTPException(status_code=404, detail="영수증을 찾을 수 없습니다.")
    return receipt


@router.post(
    "",
    response_model=ReceiptDetail,
    status_code=201,
    summary="영수증 사진 업로드 → 초안(pending) 반환",
    responses={409: {"description": "같은 영수증이 이미 등록됨. 응답에 기존 receipt_id가 들어 있다"}},
)
def upload_receipt(
    file: UploadFile = File(description="영수증 사진 (jpg/png/webp, 최대 10MB)"),
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
    ocr: OcrEngine = Depends(get_ocr_engine),
):
    # 1~3. 확장자·크기 검사, 보정 후 uploads/{fridge_id}/{uuid}.jpg로 저장
    image = load_image(read_upload(file))
    fridge_id = access.fridge.id
    relative_path = f"{fridge_id}/{uuid.uuid4().hex}.jpg"
    absolute_path = Path(settings.upload_dir) / relative_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(absolute_path, "JPEG", quality=90)

    try:
        # 4. 영수증 바코드 = 중복 등록 방지 키. 못 읽어도 업로드는 진행한다.
        receipt_barcode = barcode_service.decode_receipt_barcode(image)
        if receipt_barcode:
            existing_id = db.scalar(
                select(Receipt.id).where(Receipt.fridge_id == fridge_id, Receipt.receipt_barcode == receipt_barcode)
            )
            if existing_id:
                _remove_file(relative_path)
                return JSONResponse(
                    status_code=409, content={"detail": DUPLICATE_RECEIPT, "receipt_id": existing_id}
                )

        # 5. OCR(빠른 모델 → 필요하면 정밀 모델) → 파서 → 품목별 카테고리·소비기한 (상품코드가 있으면 C005 조회)
        try:
            boxes = read_receipt(ocr, str(absolute_path))
        except OcrBusyError:
            raise HTTPException(status_code=503, detail=OCR_BUSY, headers={"Retry-After": "10"}) from None
        except Exception:
            # 인식 실패는 "품목 0개" 초안으로 이어 간다. 사용자가 직접 추가할 수 있다.
            logger.exception("영수증 OCR 실패")
            if settings.ocr_fail_closed:
                raise HTTPException(status_code=503, detail="영수증 인식 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.") from None
            boxes = []
        parsed = parse_receipt(boxes)
        purchased_on = plausible_purchase_date(parsed.purchased_on)

        # 6. 초안 저장
        receipt = Receipt(
            fridge_id=fridge_id,
            uploaded_by=access.user.id,
            status="pending",
            store_name=(parsed.store_name or "")[:100] or None,
            purchased_on=purchased_on,
            total_amount=parsed.total_amount,
            receipt_barcode=receipt_barcode,
            image_path=relative_path,
            raw_lines=parsed.lines,
            draft_items=build_draft(db, parsed, purchased_on or today()),
        )
        db.add(receipt)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing_id = receipt_barcode and db.scalar(
                select(Receipt.id).where(Receipt.fridge_id == fridge_id, Receipt.receipt_barcode == receipt_barcode)
            )
            if not existing_id:
                raise
            # 같은 영수증을 동시에 올려서 unique(fridge_id, receipt_barcode)에 걸린 경우
            _remove_file(relative_path)
            return JSONResponse(status_code=409, content={"detail": DUPLICATE_RECEIPT, "receipt_id": existing_id})
    except Exception:
        _remove_file(relative_path)
        raise
    return _detail(db, receipt)


@router.get("", response_model=list[ReceiptSummary], summary="영수증 목록")
def list_receipts(
    status: Literal["pending", "confirmed"] | None = None,
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
):
    query = select(Receipt).where(Receipt.fridge_id == access.fridge.id)
    if status:
        query = query.where(Receipt.status == status)
    receipts = db.scalars(query.order_by(Receipt.created_at.desc(), Receipt.id.desc())).all()
    confirmed_counts = dict(
        db.execute(
            select(ReceiptItem.receipt_id, func.count())
            .join(Receipt, Receipt.id == ReceiptItem.receipt_id)
            .where(Receipt.fridge_id == access.fridge.id)
            .group_by(ReceiptItem.receipt_id)
        ).all()
    )
    return [
        ReceiptSummary(
            id=receipt.id,
            status=receipt.status,
            store_name=receipt.store_name,
            purchased_on=receipt.purchased_on,
            total_amount=receipt.total_amount,
            item_count=(
                len(receipt.draft_items or []) if receipt.status == "pending" else confirmed_counts.get(receipt.id, 0)
            ),
            created_at=receipt.created_at,
            confirmed_at=receipt.confirmed_at,
        )
        for receipt in receipts
    ]


@router.get("/{receipt_id}", response_model=ReceiptDetail, summary="영수증 상세 (초안 + OCR 원문 줄)")
def get_receipt(receipt_id: int, access: FridgeAccess = Depends(get_fridge_access), db: Session = Depends(get_db)):
    return _detail(db, _get_receipt(db, access.fridge.id, receipt_id))


def _draft_matcher(receipt: Receipt):
    """확정 요청의 줄에 대응하는 초안 줄을 찾는다. line_no가 있으면 그것으로, 없으면 이름으로."""
    by_line: dict[int, dict] = {}
    by_name: dict[str, dict] = {}
    for draft in receipt.draft_items or []:
        by_line[draft.get("line_no")] = draft
        by_name.setdefault(draft.get("name"), draft)

    def match(item: ConfirmItem) -> dict | None:
        if item.line_no is not None:
            return by_line.get(item.line_no)
        return by_name.get(item.name)

    return match


@router.post("/{receipt_id}/confirm", response_model=ReceiptConfirmResponse, summary="수정한 품목으로 영수증 확정 → 냉장고에 넣기")
def confirm_receipt(
    receipt_id: int,
    payload: ReceiptConfirmRequest,
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
):
    receipt = _get_receipt(db, access.fridge.id, receipt_id, lock=True)
    if receipt.status != "pending":
        raise HTTPException(status_code=409, detail="이미 확정된 영수증입니다.")

    # 구매일: 요청 값 → 영수증에서 읽은 날짜 → 오늘
    purchased_on = payload.purchased_on or receipt.purchased_on or today()
    match_draft = _draft_matcher(receipt)
    lookup = _ProductLookup(db)

    created: list[FoodItem] = []
    for line_no, line in enumerate(payload.items, start=1):
        category = line.category or classify(line.name)
        record = ReceiptItem(
            receipt_id=receipt.id,
            line_no=line_no,
            name=line.name,
            barcode=line.barcode,
            quantity=line.quantity,
            unit_price=line.unit_price,
            amount=line.amount,
            discount=line.discount,
            category=category,
            included=line.include,
        )
        db.add(record)  # 비식품을 포함한 모든 줄이 구매 기록으로 남는다
        if not line.include:
            continue

        # 소비기한을 비웠거나 초안의 추정값을 그대로 돌려보냈으면 자동 추정으로 본다.
        # (확정하면서 구매일이나 카테고리를 고쳤다면 그 값으로 다시 계산된다)
        draft = match_draft(line)
        untouched = draft is not None and draft.get("expiry_date") == (
            line.expiry_date.isoformat() if line.expiry_date else None
        )
        if line.expiry_date is None or untouched:
            guess = estimate(line.name, purchased_on, lookup.get(line.barcode), category=category)
            expiry_date, expiry_source = guess.expiry_date, guess.expiry_source
        else:
            guess = estimate(line.name, purchased_on, category=category)
            expiry_date, expiry_source = line.expiry_date, "manual"

        total_price = line.amount - line.discount
        food = FoodItem(
            fridge_id=receipt.fridge_id,
            receipt_id=receipt.id,
            name=line.name,
            category=category,
            storage=line.storage or guess.storage,
            barcode=line.barcode,
            quantity=line.quantity,
            remaining_quantity=line.quantity,
            unit_price=line.unit_price if line.unit_price is not None else math.floor(total_price / line.quantity + 0.5),
            total_price=total_price,
            purchased_on=purchased_on,
            expiry_date=expiry_date,
            expiry_source=expiry_source,
            added_by=access.user.id,
        )
        db.add(food)
        db.flush()
        record.food_item_id = food.id
        created.append(food)

    receipt.status = "confirmed"
    receipt.confirmed_at = utcnow()
    receipt.purchased_on = purchased_on
    if payload.store_name:
        receipt.store_name = payload.store_name
    db.commit()
    return ReceiptConfirmResponse(
        receipt_id=receipt.id,
        created_items=[ItemOut.model_validate(food) for food in created],
        skipped=len(payload.items) - len(created),
    )


@router.delete("/{receipt_id}", status_code=204, summary="초안(pending) 영수증 삭제")
def delete_receipt(receipt_id: int, access: FridgeAccess = Depends(get_fridge_access), db: Session = Depends(get_db)):
    receipt = _get_receipt(db, access.fridge.id, receipt_id)
    if receipt.status != "pending":
        raise HTTPException(status_code=409, detail="확정된 영수증은 삭제할 수 없습니다.")
    image_path = receipt.image_path
    db.delete(receipt)
    db.commit()
    _remove_file(image_path)
    return Response(status_code=204)
