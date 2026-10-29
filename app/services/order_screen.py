"""온라인 쇼핑몰 주문내역 화면 캡처(쿠팡 등) → 품목 목록.

화면은 주문마다 카드 하나이고, 카드는 위에서 아래로 날짜 / 배송 상태 / 상품 이름(화면 폭에 맞춰 잘린다) /
"12,900원 · 4개" / 버튼들이다. OCR이 점(·)을 놓쳐 "12,900원4개"로 읽는 일이 많다.

- 가격은 개당 가격이다. (쿠팡 목록에서 4개를 주문한 카드의 결제 금액이 화면 가격의 4배인 것을 2026-10-29에 확인했다)
  마켓컬리 화면은 아직 샘플이 없어서 같을 것이라고 가정한다.
- 영수증처럼 맞춰 볼 총액이 없다. total_amount는 품목 금액의 합으로 채운다.
- 상품 이름이 잘려 있으면 name_cut으로 표시한다. 취소·반품·환불 카드는 건너뛴다.
"""

import re
from datetime import date

# "12,900원4개", "12,900원 · 4개", "12,900 원 4 개"
PRICE_QTY_RE = re.compile(r"(\d[\d,.]*)\s*원\s*[·ㆍ.,:|]?\s*(\d{1,3})\s*개")
# "2026. 10. 29", "2026, 10, 29", "2026.10.29"
ORDER_DATE_RE = re.compile(r"(?<!\d)(20\d{2})\s*[.,/-]\s*(\d{1,2})\s*[.,/-]\s*(\d{1,2})(?!\d)")
# 주문 화면에서만 나오는 낱말. 가격·수량 줄과 함께 있어야 주문 화면으로 본다.
SCREEN_MARKERS = re.compile(r"배송\s*[·ㆍ.]?\s*주문\s*관리|배송\s*조회|바로\s*구매|주문\s*내역|배송\s*완료|배송\s*중|상품\s*준비\s*중|배송\s*시작")
STATUS_RE = re.compile(r"^(?:상품\s*준비\s*중|배송\s*준비\s*중|배송\s*시작|배송\s*중|배송\s*완료|결제\s*완료|주문\s*접수|주문\s*완료|취소|반품|환불)")
CANCELLED_RE = re.compile(r"취소|반품|환불")
BUTTON_RE = re.compile(r"배송\s*[·ㆍ.]?\s*주문\s*관리|배송\s*조회|바로\s*구매|장바구니|리뷰|재구매|주문\s*상세")
CUT_RE = re.compile(r"(?:\.{1,3}|…|,\s*…?)\s*$")
TRAILING_JUNK_RE = re.compile(r"[\s.,…·ㆍ]+$")
MAX_NAME_LINES = 2  # 상품 이름은 화면에서 두 줄까지 감긴다
MAX_QUANTITY = 99


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _order_date(text: str) -> date | None:
    match = ORDER_DATE_RE.search(text)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def is_order_screen(lines: list[str]) -> bool:
    """가격·수량 줄("12,900원 4개")이 있고, 주문 화면의 낱말(배송조회, 바로구매 ...)도 있다."""
    return any(PRICE_QTY_RE.search(line) for line in lines) and any(SCREEN_MARKERS.search(line) for line in lines)


def _store_name(lines: list[str]) -> str | None:
    text = " ".join(lines)
    if "로켓" in text:  # 로켓배송·로켓프레시·판매자로켓은 쿠팡의 이름이다
        return "쿠팡"
    if re.search(r"샛별|컬리", text):
        return "마켓컬리"
    return None


def parse_order_screen(grouped: list[tuple[str, float]]):
    """(줄 텍스트, 확신도) 목록 → ParsedReceipt. 카드마다 날짜 → 상태 → 이름 → 가격·수량 순서를 따른다."""
    from app.services.receipt_parser import ParsedItem, ParsedReceipt  # 순환 import를 피하려고 함수 안에서

    items: list[ParsedItem] = []
    cards_date: date | None = None  # 첫 품목이 속한 카드의 날짜
    current_date: date | None = None
    in_card, skip_card = False, False
    name_lines: list[tuple[str, float]] = []
    texts = []

    for raw, confidence in grouped:
        text = _clean(raw)
        texts.append(text)
        if not text:
            continue
        if (found := _order_date(text)) and len(text) <= 20:  # 날짜만 있는 머리글 줄
            current_date, in_card, skip_card, name_lines = found, True, False, []
            continue
        if STATUS_RE.match(text.replace(" ", "")):  # 배송중, 배송완료 ... (OCR이 공백을 빼먹는다)
            in_card, skip_card, name_lines = True, bool(CANCELLED_RE.search(text)), []
            continue
        if match := PRICE_QTY_RE.search(text):
            used = name_lines[-MAX_NAME_LINES:]
            price, quantity = _price(match.group(1)), int(match.group(2))
            name = _clean(" ".join(line for line, _ in used))
            cut = bool(CUT_RE.search(name))
            # 이름 보정(fix_name)은 쓰지 않는다: 화면 글자는 OCR이 거의 틀리지 않고, 옷·브랜드 이름이 많아서
            # 자모 하나 차이로 식품 낱말로 바뀐다. ("와이드 팬츠"의 "와이드"가 "에이드"로 바뀌었다)
            name = TRAILING_JUNK_RE.sub("", name)
            if in_card and not skip_card and name and price and 1 <= quantity <= MAX_QUANTITY:
                score = (sum(s for _, s in used) / len(used) + confidence) / 2
                # 화면의 가격은 개당 가격이다
                items.append(ParsedItem(len(items) + 1, name, float(quantity), price, price * quantity, 0, None, round(score, 3), cut))
                cards_date = cards_date or current_date
            name_lines = []  # 한 카드에 상품이 여럿이면 다음 상품의 이름이 이어서 나온다. 카드는 버튼 줄이나 다음 날짜에서 끝난다
            continue
        if BUTTON_RE.search(text):
            in_card, name_lines = False, []
            continue
        if in_card:
            name_lines.append((text, confidence))

    return ParsedReceipt(
        store_name=_store_name(texts),
        purchased_on=cards_date or current_date,
        total_amount=sum(item.amount for item in items) or None,
        items=items,
        lines=texts,
        kind="order_screen",
    )


def _price(token: str) -> int | None:
    digits = re.sub(r"[,.]", "", token)
    return int(digits) if digits.isdigit() and int(digits) > 0 else None
