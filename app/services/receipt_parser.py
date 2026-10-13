"""영수증 OCR 박스 목록 → 품목 목록. OCR 없이 테스트할 수 있는 순수 함수만 둔다.

처리 순서: 줄 묶기 → 정규화 → 품목 구간 찾기 → 줄 해석 → 메타 정보(매장명·구매일·총액)
"""

import re
import statistics
from dataclasses import dataclass, field
from datetime import date

from app.services.ocr.base import OcrBox


@dataclass
class ParsedItem:
    line_no: int
    name: str
    quantity: float
    unit_price: int | None
    amount: int
    discount: int = 0
    barcode: str | None = None
    confidence: float = 0.0


@dataclass
class ParsedReceipt:
    store_name: str | None = None
    purchased_on: date | None = None
    total_amount: int | None = None
    items: list[ParsedItem] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


# ── 패턴 ────────────────────────────────────────────────────────────────

HEADER_RE = re.compile(r"상품명|품명|품\s*목")
END_RE = re.compile(
    r"합\s*계|총\s*액|총\s*금액|과세\s*물품|면세\s*물품|부\s*가\s*세|받을\s*금액|결제\s*금액"
    r"|판매\s*총액|공급가|카드\s*승인|신용\s*카드|총\s*구매"
)
DISCOUNT_RE = re.compile(r"할인|행사|쿠폰|D/?C|에누리", re.IGNORECASE)
# 주소·대표·전화·TEL·POS는 낱말 첫머리에 올 때만 본다. ("맥주소시지"의 "주소" 같은 오매칭 방지)
NOISE_RE = re.compile(
    r"영\s*수\s*증|사업자|계산원|캐셔|교환|환불|(?:^|[\s\[(])(?:주소|대표|전화|(?:TEL|POS)(?![A-Za-z]))",
    re.IGNORECASE,
)
HEADER_WORDS = {"상품명", "품명", "품목", "상품코드", "코드", "바코드", "단가", "수량", "금액"}
STORE_RE = re.compile(
    r"마트|편의점|GS25|CU|세븐일레븐|이마트|홈플러스|롯데마트|코스트코|하나로|노브랜드|슈퍼|마켓|점$"
)
# 구분자가 공백뿐인 형태는 받지 않는다: "2000 1 2000" 같은 가격 줄이 2000-01-20으로 읽히기 때문.
DATE_RE = re.compile(r"(?<![\d-])(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})(?!\d)")
COMPACT_DATE_RE = re.compile(r"(?<![\d-])(20\d{2})(\d{2})(\d{2})(?!\d)")
# 시각, 전화번호, 사업자번호가 있는 줄은 품목이 아니다.
META_RE = re.compile(r"\d{1,2}:\d{2}|\d{2,4}-\d{3,4}-\d{4}|\d{3}-\d{2}-\d{5}")
PAY_TOTAL_RE = re.compile(r"받을\s*금액|결제\s*금액")
SUM_TOTAL_RE = re.compile(r"합\s*계|총\s*액|총\s*금액|판매\s*총액")
SUBTOTAL_RE = re.compile(r"과세|면세|부\s*가\s*세|할인|에누리|수량")
CODE_RE = re.compile(r"\d{8,14}")
GROUPED_NUMBER_RE = re.compile(r"[1-9]\d{0,2}(?:[,.]\d{3})+")  # OCR이 쉼표를 마침표로 읽는 경우 포함
HANGUL_RE = re.compile(r"[가-힣]")

_FULLWIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}
_FULLWIDTH[0x3000] = 0x20
_FULLWIDTH[0xFFE6] = ord("₩")

_MARKS_RE = re.compile(r"^(?:[*#★☆]+\s*|[(\[]\s*면세\s*[)\]]\s*|P\s+(?=\S))+")
_SEQUENCE_RE = re.compile(r"^(?:\d{1,3}\s+(?=[^\d\s,.\-])|0\d{1,2}(?=[^\d\s,.\-]))")
_TRAILING_MARKS_RE = re.compile(r"\s*[*#★☆]+$")


# ── 1. 줄 묶기 ───────────────────────────────────────────────────────────


def group_lines(boxes: list[OcrBox]) -> list[tuple[str, float]]:
    """박스를 줄 단위로 묶어 (줄 텍스트, 평균 score) 목록으로 돌려준다."""
    boxes = [box for box in boxes if box.text and box.text.strip()]
    if not boxes:
        return []

    def center_y(box: OcrBox) -> float:
        return (box.y0 + box.y1) / 2

    tolerance = statistics.median(abs(box.y1 - box.y0) for box in boxes) * 0.5
    rows: list[list[OcrBox]] = []
    for box in sorted(boxes, key=center_y):
        if rows:
            row = rows[-1]
            row_center = sum(center_y(b) for b in row) / len(row)
            if abs(center_y(box) - row_center) <= tolerance:
                row.append(box)
                continue
        rows.append([box])

    lines = []
    for row in rows:
        row.sort(key=lambda b: b.x0)
        text = " ".join(b.text.strip() for b in row)
        lines.append((text, sum(b.score for b in row) / len(row)))
    return lines


# ── 2. 정규화 ────────────────────────────────────────────────────────────


def normalize_line(text: str) -> str:
    text = text.translate(_FULLWIDTH)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(\d)\s*,\s*(\d{3})(?!\d)", r"\1,\2", text)  # "2, 980" → "2,980"
    text = re.sub(r"-\s+(?=\d)", "-", text)  # "- 500" → "-500"
    # 줄 앞의 순번(001, 01)과 면세·행사 표시(*, #, ★, (면세), P)
    text = _MARKS_RE.sub("", text)
    text = _SEQUENCE_RE.sub("", text, count=1)
    text = _MARKS_RE.sub("", text)
    return _TRAILING_MARKS_RE.sub("", text).strip()


# ── 3. 품목 구간 ─────────────────────────────────────────────────────────


def find_item_section(lines: list[str]) -> tuple[int, int]:
    """품목이 있는 줄 범위 [start, end)."""
    start = 0
    for index, line in enumerate(lines):
        if HEADER_RE.search(line) and not END_RE.search(line):
            start = index + 1
            break
    end = len(lines)
    for index in range(start, len(lines)):
        if END_RE.search(lines[index]):
            end = index
            break
    return start, end


# ── 4. 줄 해석 ───────────────────────────────────────────────────────────


def _to_int(token: str) -> int | None:
    """금액 숫자 토큰("1,234", "-500", "2,980원")이면 정수로, 아니면 None."""
    token = token.lstrip("₩\\").removesuffix("원")
    negative = False
    if token.startswith("-"):
        negative, token = True, token[1:]
    elif token.endswith("-"):  # "500-"처럼 뒤에 붙는 음수 표기
        negative, token = True, token[:-1]
    if GROUPED_NUMBER_RE.fullmatch(token):
        value = int(re.sub(r"[,.]", "", token))
    elif token.isascii() and token.isdigit():
        value = int(token)
    else:
        return None
    return -value if negative else value


@dataclass
class _Line:
    name: str
    numbers: list[int]
    barcode: str | None


def _split_line(text: str) -> _Line:
    """줄 끝에서부터 숫자 토큰을 모으고, 나머지를 상품명으로 본다."""
    tokens = text.split()
    numeric: list[tuple[str, int]] = []
    barcode: str | None = None
    cut = len(tokens)
    while cut > 0:
        token = tokens[cut - 1]
        if CODE_RE.fullmatch(token):
            barcode = token
        else:
            value = _to_int(token)
            if value is None:
                break
            numeric.append((token, value))
        cut -= 1
    numeric.reverse()

    name_tokens: list[str] = []
    for token in tokens[:cut]:
        if barcode is None and CODE_RE.fullmatch(token):
            barcode = token  # 상품명 앞에 찍힌 상품코드
        else:
            name_tokens.append(token)
    # 숫자가 4개 이상이면 끝의 세 개만 (단가, 수량, 금액)이고 나머지는 상품명의 일부다. ("콜라 500 2,000 1 2,000")
    if name_tokens and len(numeric) > 3:
        name_tokens += [token for token, _ in numeric[:-3]]
        numeric = numeric[-3:]
    return _Line(" ".join(name_tokens), [value for _, value in numeric], barcode)


def _assign_numbers(numbers: list[int], waiting_quantity: int | None = None) -> tuple[float, int | None, int]:
    """숫자들을 (수량, 단가, 금액)으로 배정한다."""
    numbers = numbers[-3:]
    if len(numbers) == 3:
        unit, quantity, amount = numbers
        if unit * quantity == amount and quantity > 0:
            if quantity > 99 >= unit:  # (수량, 단가, 금액) 순서로 찍힌 영수증
                unit, quantity = quantity, unit
            return float(quantity), unit, amount
        # 검증 실패. 금액이 단가의 정수배면 그 배수가 수량이다.
        # (OCR이 수량 한 글자를 잘못 읽은 경우: "2,980 7 2,980" → 수량 1)
        if unit > 99 and amount % unit == 0 and 0 < amount // unit <= 99:
            return float(amount // unit), unit, amount
        # 그 밖에는 99 이하인 수를 수량으로 보고, 금액은 마지막 수
        if 0 < quantity <= 99:
            return float(quantity), unit, amount
        if 0 < unit <= 99:
            return float(unit), quantity, amount
        return 1.0, None, amount
    if len(numbers) == 2:
        first, amount = numbers
        if 0 < first <= 99:
            return float(first), (amount // first if amount % first == 0 else None), amount
        if first == amount:
            return 1.0, first, amount
        if first > 0 and amount % first == 0 and amount // first <= 99:
            return float(amount // first), first, amount
        return 1.0, (first or None), amount
    amount = numbers[0]
    quantity = waiting_quantity or 1
    return float(quantity), (amount // quantity if amount % quantity == 0 else None), amount


def _is_triplet(numbers: list[int]) -> bool:
    return len(numbers) >= 3 and min(numbers[-3:]) > 0 and numbers[-3] * numbers[-2] == numbers[-1]


def _is_header_only(name: str) -> bool:
    words = re.split(r"[\s/|·]+", name)
    return all(word in HEADER_WORDS for word in words if word)


@dataclass
class _Waiting:
    """상품명만 있는 줄. 다음 줄의 숫자를 기다린다."""

    name: str
    barcode: str | None
    confidence: float
    quantity: int | None = None
    is_discount: bool = False


def parse_items(lines: list[tuple[str, float]]) -> list[ParsedItem]:
    """품목 구간의 (정규화된 줄, confidence) 목록을 품목으로 해석한다."""
    items: list[ParsedItem] = []
    waiting: _Waiting | None = None

    def add_item(name: str, numbers: list[int], barcode: str | None, confidence: float) -> None:
        quantity, unit_price, amount = _assign_numbers(numbers, waiting.quantity if waiting else None)
        items.append(
            ParsedItem(len(items) + 1, name, quantity, unit_price, amount, 0, barcode, round(confidence, 3))
        )

    for text, confidence in lines:
        if not text:
            continue
        if NOISE_RE.search(text) or META_RE.search(text) or DATE_RE.search(text):
            waiting = None
            continue

        line = _split_line(text)
        named = len(line.name) > 1 and not _is_header_only(line.name)

        if not line.numbers:
            if named:  # (b) 상품명만 → 다음 줄의 숫자를 기다린다
                waiting = _Waiting(line.name, line.barcode, confidence, is_discount=bool(DISCOUNT_RE.search(line.name)))
            elif line.barcode and waiting:
                waiting.barcode = line.barcode
            else:
                waiting = None
            continue

        # (d) 할인 줄: 음수 금액이거나 할인 낱말이 있는 줄 → 직전 품목의 discount에 더한다
        is_discount = line.numbers[-1] < 0
        if named:
            is_discount = is_discount or (bool(DISCOUNT_RE.search(line.name)) and not _is_triplet(line.numbers))
        elif waiting and waiting.is_discount:
            is_discount = True
        if is_discount:
            if items:
                items[-1].discount += abs(line.numbers[-1])
            waiting = None
            continue

        if not named:  # (c) 코드 + 숫자만 → 기다리던 상품명에 붙인다
            if waiting:
                add_item(
                    waiting.name, line.numbers, line.barcode or waiting.barcode, (waiting.confidence + confidence) / 2
                )
            waiting = None
            continue

        # (a) 상품명 + 숫자
        if len(line.numbers) == 1 and 0 < line.numbers[0] < 10:
            # 숫자가 10 미만 하나뿐이면 수량이다. 금액은 다음 줄에 있다.
            waiting = _Waiting(line.name, line.barcode, confidence, quantity=line.numbers[0])
            continue
        waiting = None
        add_item(line.name, line.numbers, line.barcode, confidence)

    return items


# ── 5. 메타 정보 ─────────────────────────────────────────────────────────


def find_store_name(lines: list[str]) -> str | None:
    top = [line for line in lines[:5] if line]
    for line in top:
        if STORE_RE.search(line) and not META_RE.search(line):
            return line
    for line in top:
        if len(HANGUL_RE.findall(line)) >= 2 and not NOISE_RE.search(line) and not _is_header_only(line):
            return line
    return None


def find_purchase_date(lines: list[str]) -> date | None:
    for pattern in (DATE_RE, COMPACT_DATE_RE):
        for line in lines:
            for year, month, day in pattern.findall(line):
                try:
                    return date(int(year), int(month), int(day))
                except ValueError:
                    continue  # 13월 45일 같은 잘못된 날짜는 무시
    return None


def _last_number(line: str) -> int | None:
    for token in reversed(line.split()):
        value = _to_int(token)
        if value is not None and not CODE_RE.fullmatch(token):
            return abs(value)
    return None


def find_total(lines: list[str]) -> int | None:
    """받을금액·결제금액 줄을 합계·총액 줄보다 우선하고, 그 줄의 마지막 숫자를 쓴다."""
    for line in lines:
        if PAY_TOTAL_RE.search(line) and (value := _last_number(line)) is not None:
            return value
    for line in lines:
        if SUM_TOTAL_RE.search(line) and not SUBTOTAL_RE.search(line) and (value := _last_number(line)) is not None:
            return value
    return None


# ── 진입점 ───────────────────────────────────────────────────────────────


def parse_receipt(boxes: list[OcrBox]) -> ParsedReceipt:
    grouped = [(normalize_line(text), confidence) for text, confidence in group_lines(boxes)]
    lines = [text for text, _ in grouped]
    start, end = find_item_section(lines)
    return ParsedReceipt(
        store_name=find_store_name(lines),
        purchased_on=find_purchase_date(lines),
        total_amount=find_total(lines),
        items=parse_items(grouped[start:end]),
        lines=lines,
    )
