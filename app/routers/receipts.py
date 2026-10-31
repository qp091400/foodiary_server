"""영수증 업로드(초안) → 확정. OCR은 100% 정확하지 않으므로 반드시 2단계를 거친다."""

import io
import json
import logging
import math
import time
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import FridgeAccess, get_fridge_access
from app.middleware import extra_ocr_request
from app.models import ConsumptionLog, FoodItem, Receipt, ReceiptItem
from app.schemas import (
    BatchUploadResult,
    ConfirmItem,
    DraftItem,
    ItemOut,
    ReceiptBatchResponse,
    ReceiptConfirmRequest,
    ReceiptConfirmResponse,
    ReceiptDetail,
    ReceiptItemOut,
    ReceiptSummary,
)
from app.services import barcode as barcode_service
from app.services.expiry import OTHER, classify, estimate
from app.services.foodsafety import ProductInfo, lookup_barcode
from app.services.ocr import OcrEngine, get_ocr_engine
from app.services.ocr.base import OcrBox, OcrBusyError, next_ticket
from app.services.ocr.paddle import Verdict
from app.services.receipt_parser import ParsedReceipt, merge_same_name, parse_receipt
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
WARN_NAME_CUT = "상품 이름이 화면에서 잘려 있어요. 이름을 확인해 주세요"
OCR_BUSY = "다른 영수증을 분석 중입니다. 잠시 후 다시 시도해 주세요."
TOO_MANY_REQUESTS = "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
BATCH_TIME_UP = "사진이 많아 정해진 시간 안에 읽지 못했어요. 이 사진부터 다시 올려 주세요."
BATCH_RETRY_SECONDS = 5
# 사진 여러 장 업로드 한 번에 받는 최대 장수. 장수 때문에 막히지는 않게 넉넉하게 두고(요청 시간은 batch_seconds가 지킨다),
# 앱이 사진을 나눠서 보내면 더 많이도 올릴 수 있다. 이 수는 한 요청이 메모리와 시간을 무한정 쓰지 못하게 하는 안전장치다.
MAX_BATCH_FILES = 50  # TODO: balance
# 합계가 맞지 않아도 세로 사진에서 이만큼 품목을 읽었으면 사진 방향은 맞다고 본다
UPRIGHT_MIN_ITEMS = 3  # TODO: balance


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
    for item in merge_same_name(parsed.items):
        product = lookup.get(item.barcode)
        guess = estimate(item.name, purchased_on, product)
        # 쇼핑몰 주문에는 옷·생활용품이 많이 섞여 있다. 식품으로 알아본 것만 넣고 모르는 것은 제외로 둔다(사용자가 켠다).
        food = guess.is_food and not (parsed.kind == "order_screen" and guess.category == OTHER)
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
                is_food=food,
                include=food,
                expiry_date=guess.expiry_date,
                expiry_source=guess.expiry_source,
                product_name=product.name if product else None,
                confidence=item.confidence,
                name_cut=item.name_cut,
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


def judge_reading(parsed: ParsedReceipt, *, landscape: bool) -> Verdict:
    """읽은 결과 판정. 합계가 맞으면 GOOD.

    합계가 안 맞아도 세로 사진에서 품목을 몇 개 이상 읽었으면 방향은 맞다(UPRIGHT). 총액 줄이 없는 영수증도 있다.
    가로 사진이거나 품목을 거의 못 읽었으면 사진이 돌아가 있을 수 있다(DOUBTFUL).
    """
    if checks_out(parsed):
        return Verdict.GOOD
    if not landscape and len(parsed.items) >= UPRIGHT_MIN_ITEMS:
        return Verdict.UPRIGHT
    return Verdict.DOUBTFUL


def read_receipt(
    ocr: OcrEngine, image_path: str, *, landscape: bool, wait: float = 0, ticket: int | None = None
) -> list[OcrBox]:
    """OCR. 합계가 맞지 않으면 엔진이 대비를 높이거나 사진을 돌려 다시 읽는다 (그럴 수 있는 엔진만).

    끝내 합계가 맞지 않으면(총액 줄이 없는 영수증 등) 품목을 가장 많이 읽은 결과를 고른다. 품목 수가 같으면 처음 읽은
    결과를 그대로 쓴다: 영수증이 아닌 사진(화면 캡처 등)에서 글자 무게로 고르면 거꾸로 돌려 읽은 결과가 뽑힌다.

    다른 사진을 읽는 중이면 wait초까지 순서를 기다린다 (그럴 수 있는 엔진만). 기다리다 시간이 지나면 OcrBusyError.
    ticket은 요청 번호다. 번호가 작은 요청의 사진부터 읽는다.
    """
    options = {"wait": wait, "ticket": ticket} if getattr(ocr, "can_queue", False) else {}
    if not getattr(ocr, "can_reread", False):
        return ocr.recognize(image_path, **options)
    return ocr.recognize(
        image_path,
        judge=lambda boxes: judge_reading(parse_receipt(boxes), landscape=landscape),
        rank=lambda boxes: len(parse_receipt(boxes).items),
        **options,
    )


def draft_warnings(item_count: int, items_sum: int, total_amount: int | None, *, name_cut: bool = False) -> list[str]:
    if item_count == 0:
        return [WARN_NO_ITEMS]
    warnings = []
    if not total_amount:  # 합계를 못 읽으면 빠진 품목이 있어도 알 수 없다
        warnings.append(WARN_NO_TOTAL)
    elif abs(items_sum - total_amount) > total_amount * 0.01:
        warnings.append(WARN_SUM_MISMATCH)
    if name_cut:
        warnings.append(WARN_NAME_CUT)
    return warnings


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
        warnings=draft_warnings(
            count, items_sum, receipt.total_amount,
            name_cut=receipt.status == "pending" and any(item.name_cut for item in draft_items),
        ),
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
    # 다른 사람이 읽는 중이면 바로 거절하지 않고 순서를 기다린다
    return _create_draft(file, access, db, ocr, settings.ocr_queue_wait_seconds)


@dataclass
class BatchOutcome:
    """여러 장 업로드에서 사진 한 장의 결과. payload는 성공했을 때 그 장의 결과물."""

    index: int
    filename: str | None
    status_code: int
    payload: Any = None
    detail: str | None = None
    receipt_id: int | None = None
    retry_after: int | None = None

    @property
    def ok(self) -> bool:
        return self.payload is not None


@dataclass
class BatchSteps:
    """사진 한 장을 처리하는 세 단계. 한 사진은 prepare → read → finish 순서이고, 사진 여러 장의 read가 겹쳐 돌 수 있다.

    prepare와 finish는 요청 스레드에서 차례로 돈다(DB·파일). read는 OCR만 하고 DB를 쓰지 않아서 여러 스레드에서 동시에 돈다.
    prepare가 돌려준 사진 객체는 discard()를 가져야 한다. 그 뒤 단계가 실패하면 저장한 파일을 지우려고 부른다.
    """

    prepare: Callable[[UploadFile], Any]  # → 사진 객체. 이미 등록된 영수증이면 409 JSONResponse, 실패는 HTTPException
    read: Callable[[Any, float, int | None], Any]  # (사진, 기다릴 수 있는 초, 요청 번호) → OCR 결과
    finish: Callable[[Any, Any], Any]  # (사진, OCR 결과) → 결과물. 이미 등록된 영수증이면 409 JSONResponse


def _done(function: Callable[..., Any], *args: Any) -> Future:
    """function을 지금 이 스레드에서 돌리고 결과(또는 오류)를 담은 Future를 돌려준다."""
    future: Future = Future()
    try:
        future.set_result(function(*args))
    except Exception as error:
        future.set_exception(error)
    return future


def run_batch(
    request: Request,
    files: list[UploadFile],
    steps: BatchSteps,
    *,
    ocr: OcrEngine | None = None,
    db: Session | None = None,
) -> list[BatchOutcome]:
    """사진을 보낸 순서대로 처리한다. 한 장이 실패해도(사유를 적고) 나머지를 계속한다.

    실패는 HTTPException으로 오고, 이미 등록된 영수증(409 JSONResponse)도 실패로 적는다.
    영수증 한도는 요청 한 번을 미들웨어가 이미 세었으므로 둘째 장부터 한 장씩 더 센다.

    OCR 엔진이 여러 개이면(ocr.engines) 사진 여러 장을 엔진 수만큼 한꺼번에 읽는다. 사진은 차례로 저장하면서 읽기를
    시작하고, 결과는 보낸 순서대로 받아 초안으로 만든다. 같은 요청의 사진은 같은 요청 번호로 줄을 서서, 여러 사람이 동시에
    올리면 먼저 온 사람의 사진부터 읽는다 (사람 순서). 엔진이 남으면 다음 사람의 사진도 같이 읽는다.
    읽기는 엔진 수의 2배까지 미리 걸어 둔다. 한 사진이 끝나고 다음 사진이 줄에 서기 전의 짧은 틈에 다른 사람의 사진이
    엔진을 가져가면 사람들이 번갈아 읽혀서 모두 늦게 끝난다. (실제 측정: 4명이 5장씩 올리면 2명씩 짝지어 끝났다)

    요청 하나가 쓸 수 있는 시간(batch_seconds)이 있다. 다른 사람 사진 때문에 오래 걸려 시간이 다 되면, 아직 시작하지 못한
    사진은 503 + retry_after로 돌려준다. 앱은 그 사진들만 다시 올리면 된다.
    """
    if not 1 <= len(files) <= MAX_BATCH_FILES:
        raise HTTPException(status_code=400, detail=f"사진은 한 번에 1장에서 {MAX_BATCH_FILES}장까지 올릴 수 있습니다.")
    deadline = time.monotonic() + settings.batch_seconds
    ticket = next_ticket() if getattr(ocr, "can_queue", False) else None
    engines = getattr(ocr, "engines", None)  # 줄 서기를 지원하는 실제 엔진만 갖고 있다
    window = 2 * engines if engines else 1  # 한꺼번에 걸어 둘 읽기 수. 1이면 한 장씩 차례로
    outcomes: list[BatchOutcome] = []
    pending: deque[tuple[BatchOutcome, Any, Future]] = deque()  # 읽는 중인 사진, 보낸 순서

    def fail(outcome: BatchOutcome, exc: HTTPException) -> None:
        outcome.status_code, outcome.detail = exc.status_code, str(exc.detail)
        outcome.retry_after = int((exc.headers or {}).get("Retry-After", 0)) or None

    def record(outcome: BatchOutcome, result: Any) -> None:
        if isinstance(result, JSONResponse):  # 이미 등록된 영수증
            body = json.loads(result.body)
            outcome.status_code, outcome.detail, outcome.receipt_id = result.status_code, body["detail"], body.get("receipt_id")
        else:
            outcome.status_code, outcome.payload, outcome.detail = 201, result, None

    def settle() -> None:
        """가장 먼저 보낸 사진의 읽기를 기다려 초안으로 만든다."""
        outcome, photo, reading = pending.popleft()
        try:
            record(outcome, steps.finish(photo, reading.result()))
        except HTTPException as exc:
            photo.discard()
            fail(outcome, exc)
        except Exception:
            logger.exception("여러 장 업로드: %s번째 사진 처리 실패", outcome.index)
            photo.discard()
            if db is not None:
                db.rollback()

    with ThreadPoolExecutor(max_workers=window) as readers:
        for index, file in enumerate(files):
            outcome = BatchOutcome(index, file.filename, 500, detail="영수증을 처리하지 못했습니다.")
            outcomes.append(outcome)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                outcome.status_code, outcome.detail, outcome.retry_after = 503, BATCH_TIME_UP, BATCH_RETRY_SECONDS
                continue
            if index and (wait := extra_ocr_request(request)):
                outcome.status_code, outcome.detail, outcome.retry_after = 429, TOO_MANY_REQUESTS, wait
                continue
            try:
                photo = steps.prepare(file)
            except HTTPException as exc:
                fail(outcome, exc)
                continue
            except Exception:
                logger.exception("여러 장 업로드: %s번째 사진 처리 실패", index)
                if db is not None:
                    db.rollback()
                continue
            if isinstance(photo, JSONResponse):
                record(outcome, photo)
                continue
            wait = min(settings.ocr_queue_wait_seconds, remaining)
            reading = readers.submit(steps.read, photo, wait, ticket) if window > 1 else _done(steps.read, photo, wait, ticket)
            pending.append((outcome, photo, reading))
            while len(pending) >= window:
                settle()
        while pending:
            settle()
    return outcomes


@router.post(
    "/batch",
    response_model=ReceiptBatchResponse,
    summary="영수증 사진 여러 장 업로드 → 장마다 초안 또는 실패 사유",
    responses={400: {"description": f"사진이 0장이거나 {MAX_BATCH_FILES}장보다 많다"}},
)
def upload_receipts(
    request: Request,
    files: list[UploadFile] = File(description=f"영수증 사진들 (jpg/png/webp, 장당 최대 10MB, 최대 {MAX_BATCH_FILES}장)"),
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
    ocr: OcrEngine = Depends(get_ocr_engine),
):
    """보낸 순서대로 저장하고 읽는다(엔진이 여러 개면 여러 장을 동시에). 장마다 따로 저장되므로 한 장이 실패(중복·바쁨 등)해도 나머지는 초안이 된다.

    실패한 장은 `status_code`와 `detail`을 보고, 503·429면 `retry_after`초 뒤에 그 장만 다시 올리면 된다.
    """
    steps = BatchSteps(
        prepare=lambda file: _prepare_draft(file, access, db),
        read=lambda photo, wait, ticket: _read_draft(photo, ocr, wait, ticket),
        finish=lambda photo, boxes: _finish_draft(photo, boxes, access, db),
    )
    outcomes = run_batch(request, files, steps, ocr=ocr, db=db)
    results = [
        BatchUploadResult(
            index=o.index, filename=o.filename, ok=o.ok, status_code=o.status_code, receipt=o.payload,
            detail=o.detail, receipt_id=o.receipt_id, retry_after=o.retry_after,
        )
        for o in outcomes
    ]
    succeeded = sum(result.ok for result in results)
    return ReceiptBatchResponse(results=results, succeeded=succeeded, failed=len(results) - succeeded)


@dataclass
class DraftPhoto:
    """저장은 끝났고 OCR 결과를 기다리는 사진 한 장."""

    image: Image.Image
    relative_path: str
    receipt_barcode: str | None

    @property
    def path(self) -> str:
        return str(Path(settings.upload_dir) / self.relative_path)

    @property
    def landscape(self) -> bool:
        return self.image.width > self.image.height

    def discard(self) -> None:
        _remove_file(self.relative_path)


def _prepare_draft(file: UploadFile, access: FridgeAccess, db: Session) -> DraftPhoto | JSONResponse:
    """사진 한 장 → 저장. 이미 등록된 영수증이면 409 JSONResponse, 그 밖의 실패는 HTTPException. (DB를 쓴다)"""
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
    except Exception:
        _remove_file(relative_path)
        raise
    return DraftPhoto(image, relative_path, receipt_barcode)


def _read_draft(photo: DraftPhoto, ocr: OcrEngine, wait: float = 0, ticket: int | None = None) -> list[OcrBox]:
    """5. OCR. DB를 쓰지 않아서 여러 스레드에서 동시에 불러도 된다.

    wait는 다른 사진이 OCR을 쓰는 동안 순서를 기다릴 수 있는 초다. 0이면 기다리지 않고 바로 503.
    """
    try:
        return read_receipt(ocr, photo.path, landscape=photo.landscape, wait=wait, ticket=ticket)
    except OcrBusyError:
        raise HTTPException(status_code=503, detail=OCR_BUSY, headers={"Retry-After": "10"}) from None
    except Exception:
        # 인식 실패는 "품목 0개" 초안으로 이어 간다. 사용자가 직접 추가할 수 있다.
        logger.exception("영수증 OCR 실패")
        if settings.ocr_fail_closed:
            raise HTTPException(status_code=503, detail="영수증 인식 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.") from None
        return []


def _finish_draft(photo: DraftPhoto, boxes: list[OcrBox], access: FridgeAccess, db: Session):
    """OCR → 파서 → 품목별 카테고리·소비기한 (상품코드가 있으면 C005 조회) → 초안 저장. (DB를 쓴다)

    성공하면 ReceiptDetail, 같은 영수증이 그 사이 등록되었다면 409 JSONResponse.
    """
    fridge_id = access.fridge.id
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
        receipt_barcode=photo.receipt_barcode,
        image_path=photo.relative_path,
        raw_lines=parsed.lines,
        draft_items=build_draft(db, parsed, purchased_on or today()),
    )
    db.add(receipt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing_id = photo.receipt_barcode and db.scalar(
            select(Receipt.id).where(Receipt.fridge_id == fridge_id, Receipt.receipt_barcode == photo.receipt_barcode)
        )
        if not existing_id:
            raise
        # 같은 영수증을 동시에 올려서 unique(fridge_id, receipt_barcode)에 걸린 경우
        photo.discard()
        return JSONResponse(status_code=409, content={"detail": DUPLICATE_RECEIPT, "receipt_id": existing_id})
    return _detail(db, receipt)


def _create_draft(file: UploadFile, access: FridgeAccess, db: Session, ocr: OcrEngine, wait: float = 0):
    """사진 한 장 → 초안. 성공하면 ReceiptDetail, 이미 등록된 영수증이면 409 JSONResponse, 그 밖의 실패는 HTTPException."""
    photo = _prepare_draft(file, access, db)
    if isinstance(photo, JSONResponse):
        return photo
    try:
        return _finish_draft(photo, _read_draft(photo, ocr, wait), access, db)
    except Exception:
        photo.discard()
        raise


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
            unit=line.unit,
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
            unit=line.unit,
            # 영수증 단가는 한 개(한 팩) 값이라 g·ml로 센 수량과는 맞지 않는다. 그때는 비워 둔다.
            unit_price=None if line.unit != "개"
            else line.unit_price if line.unit_price is not None else math.floor(total_price / line.quantity + 0.5),
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


@router.delete(
    "/{receipt_id}",
    status_code=204,
    summary="영수증 기록 삭제 (확정된 것은 그 영수증으로 넣은 식품·구매 기록·먹음/버림 기록까지). 같은 영수증을 다시 찍을 수 있게 된다",
)
def delete_receipt(receipt_id: int, access: FridgeAccess = Depends(get_fridge_access), db: Session = Depends(get_db)):
    receipt = _get_receipt(db, access.fridge.id, receipt_id, lock=True)
    image_path = receipt.image_path
    if receipt.status == "confirmed":
        # 영수증을 찍기 전으로 되돌린다. 외래키 규칙(SET NULL·CASCADE)에 기대지 않고 직접 지워 DB 종류와 상관없이 같게 한다.
        item_ids = list(db.scalars(select(FoodItem.id).where(FoodItem.receipt_id == receipt.id)))
        if item_ids:
            db.execute(delete(ConsumptionLog).where(ConsumptionLog.food_item_id.in_(item_ids)))
        db.execute(delete(ReceiptItem).where(ReceiptItem.receipt_id == receipt.id))
        if item_ids:
            db.execute(delete(FoodItem).where(FoodItem.id.in_(item_ids)))
    # 영수증 줄이 사라지면 바코드(중복 등록 방지 키)도 함께 사라져, 같은 영수증을 다시 올릴 수 있다.
    db.delete(receipt)
    db.commit()
    _remove_file(image_path)
    return Response(status_code=204)
