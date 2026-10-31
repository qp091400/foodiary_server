"""영수증 OCR 박스 목록 → 품목 목록. OCR 없이 테스트할 수 있는 순수 함수만 둔다.

처리 순서: 줄 묶기 → 정규화 → 품목 구간 찾기 → 줄 해석 → 메타 정보(매장명·구매일·총액)
"""

import math
import re
import statistics
from dataclasses import dataclass, field, replace
from datetime import date

from app.services.name_fix import fix_name
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
    name_cut: bool = False  # 화면에서 이름이 잘려 있다(주문내역 화면)


def merge_same_name(items: list[ParsedItem]) -> list[ParsedItem]:
    """한 장 안에서 이름이 완전히 같은 줄을 하나로 합친다 (홍합살 1개 + 홍합살 1개 → 홍합살 2개).

    수량·금액·할인은 더하고, 합친 줄은 처음 나온 줄의 번호와 자리를 쓴다. 금액 합계가 그대로라 총액 검사에는 영향이 없다.
    다른 영수증이나 이미 냉장고에 있는 품목과는 합치지 않는다 (영수증마다 따로 부른다).
    """
    merged: dict[str, ParsedItem] = {}
    for item in items:
        first = merged.get(item.name)
        if first is None:
            merged[item.name] = replace(item)
            continue
        if first.unit_price != item.unit_price:
            first.unit_price = None  # 단가가 다르면 한 단가로 말할 수 없다
        first.quantity += item.quantity
        first.amount += item.amount
        first.discount += item.discount
        first.barcode = first.barcode or item.barcode
        first.confidence = min(first.confidence, item.confidence)
        first.name_cut = first.name_cut or item.name_cut
    return list(merged.values())


@dataclass
class ParsedReceipt:
    store_name: str | None = None
    purchased_on: date | None = None
    total_amount: int | None = None
    items: list[ParsedItem] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    kind: str = "receipt"  # receipt | order_screen (쇼핑몰 주문내역 화면 캡처)


# ── 패턴 ────────────────────────────────────────────────────────────────

HEADER_RE = re.compile(r"상품명|품명|품\s*목")
# "압계"는 OCR이 "합계"를 잘못 읽은 것. "할인금액 :"은 품목 아래에 붙는 할인 합계(이미 각 금액에 반영됨)다.
END_RE = re.compile(
    r"[합압]\s*계|총\s*액|총\s*금액|과세\s*물품|면세\s*물품|부\s*가\s*세|받을\s*금액|결제\s*금액"
    r"|판매\s*총액|공급가|카드\s*승인|신용\s*카드|총\s*구매|할인\s*금액\s*:|총\s*할인|할인\s*합계"
)
DISCOUNT_RE = re.compile(r"할인|행사|쿠폰|D/?C|에누리", re.IGNORECASE)
# 주소·대표·전화·TEL·POS는 낱말 첫머리에 올 때만 본다. ("맥주소시지"의 "주소" 같은 오매칭 방지)
NOISE_RE = re.compile(
    r"영\s*수\s*증|사업자|계산원|캐셔|교환|환불|(?:^|[\s\[(])(?:주소|대표|전화|(?:TEL|POS)(?![A-Za-z]))",
    re.IGNORECASE,
)
# 주소 줄. OCR이 "주소"를 잘못 읽어도("추소") 시·도, 시·군·구 이름으로 알아본다.
ADDRESS_RE = re.compile(
    r"(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청|충북|충남|전라|전북|전남|경상|경북|경남|제주)[가-힣]{0,5}"
    r"\s+[가-힣]{1,6}[시군구](?![가-힣])"
    r"|[가-힣]{1,6}시\s+[가-힣]{1,6}[구읍](?![가-힣])|[가-힣]{1,6}군\s+[가-힣]{1,6}[읍면](?![가-힣])"
)
HEADER_WORDS = {"상품명", "품명", "품목", "상품코드", "코드", "바코드", "단가", "수량", "금액"}
STORE_RE = re.compile(
    r"마트|편의점|GS25|CU|세븐일레븐|이마트|홈플러스|롯데마트|코스트코|하나로|노브랜드|슈퍼|마켓|점$"
)
STORE_TAIL_RE = re.compile(r"\s*/\s*\d.*$")
# "가게이름/사업자번호/대표자": 사업자번호(10자리) 앞의 한글 이름. 사진 배경의 글자가 위에 섞여도 이 줄은 가게 이름 줄이다.
STORE_BIZ_RE = re.compile(r"^([^/]*[가-힣][^/]*?)\s*/\s*\d{3}-?\d{2}-?\d{5}(?!\d)")
STORE_LABEL_RE = re.compile(r"(?:매장명|상호명?|가맹점명)\s*[:：]\s*(.+)")
RECEIPT_WORD_RE = re.compile(r"\[?\s*영\s*수?\s*증\s*\]?")
# 구분자가 공백뿐인 형태는 받지 않는다: "2000 1 2000" 같은 가격 줄이 2000-01-20으로 읽히기 때문.
# 날짜 바로 뒤에 시각이 붙어 있어도 읽는다: OCR이 공백을 빼먹은 "2026-10-1214:22:05"
DATE_RE = re.compile(
    r"(?<![\d-])(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})(?:(?!\d)|(?=\d{1,2}:\d{2}))"
)
COMPACT_DATE_RE = re.compile(r"(?<![\d-])(20\d{2})(\d{2})(\d{2})(?!\d)")
# 연도가 두 자리인 날짜("판매일:26-10-24 16:51")는 날짜 이름표가 있는 줄에서만 읽는다. 전화번호 등과 헷갈리기 쉽다.
SHORT_DATE_RE = re.compile(r"(?<![\d-])(\d{2})[-./](\d{1,2})[-./](\d{1,2})(?:(?!\d)|(?=\d{1,2}:\d{2}))")
DATE_LABEL_RE = re.compile(r"판매|거래|구매|일시|일자|날짜")
# 시각, 전화번호, 사업자번호가 있는 줄은 품목이 아니다.
META_RE = re.compile(r"\d{1,2}:\d{2}|\d{2,4}-\d{3,4}-\d{4}|\d{3}-\d{2}-\d{5}")
PAY_TOTAL_RE = re.compile(r"받을\s*금액|결제\s*금액")
SUM_TOTAL_RE = re.compile(r"[합압]\s*계|총\s*액|총\s*금액|판매\s*총액")
SUBTOTAL_RE = re.compile(r"과세|면세|부\s*가\s*세|할인|에누리|수량")
CODE_RE = re.compile(r"\d{8,14}")
GROUPED_NUMBER_RE = re.compile(r"[1-9]\d{0,2}(?:[,.]\d{3})+")  # OCR이 쉼표를 마침표로 읽는 경우 포함
HANGUL_RE = re.compile(r"[가-힣]")
# 이보다 작은 금액은 품목으로 보지 않는다: 세트 구성품(0원), 주소 번지나 안내 문구에서 떨어져 나온 숫자.
MIN_ITEM_AMOUNT = 100  # TODO: balance
# 사진 기울기 보정: 이보다 작으면 보정하지 않고, 이보다 큰 상자는 세로 글자 등으로 보고 빼고 잰다
MIN_SKEW = math.radians(1)  # TODO: balance
MAX_SKEW = math.radians(30)

_FULLWIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}
_FULLWIDTH[0x3000] = 0x20
_FULLWIDTH[0xFFE6] = ord("₩")

_MARKS_RE = re.compile(r"^(?:[*#★☆]+\s*|[(\[]\s*면세\s*[)\]]\s*|P\s+(?=\S))+")
# 순번(1, 001)과 POS 상품코드(0004, 000123). 8자리 이상은 바코드로 따로 읽는다. "2080 치약"의 2080은 상품명이다.
# "이13", "O13"은 OCR이 순번 013의 0을 잘못 읽은 것이다.
_SEQUENCE_RE = re.compile(
    r"^(?:(?:\d{1,3}|0\d{3}|\d{5,7})\s+(?=[^\d\s,.\-])|0\d{1,2}(?=[^\d\s,.\-])|[이Oo]\d{2}\s*(?=[^\d\s,.\-]))"
)
# 숫자만 있는 줄 맨 앞의 저울·매장 상품코드("203250 14,582 1 14,582"). 금액으로 세지 않는다.
_PLU_RE = re.compile(r"^(?:0\d{3,4}|\d{6,7})\s+(?=-?[\d,.]+(?:\s|$))")
# 용량 단위를 잘못 읽은 것: "500m7", "500mI" → "500ml"
_ML_RE = re.compile(r"(?<=\d)\s?m[l7I1|](?![A-Za-z0-9])")
_TRAILING_MARKS_RE = re.compile(r"\s*[*#★☆]+$")
# 금액 모양("1,5OO", "l2,000원")인데 숫자 자리에 비슷한 글자가 섞인 토큰. 쉼표 자리 모양이 있을 때만 고친다.
_LOOKALIKE_AMOUNT_RE = re.compile(r"(?<!\S)(-?)([\dOolI|]{1,3}(?:[,.][\dOolI|]{3})+)(원?)(?!\S)")
_DIGIT_LOOKALIKES = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1"})
# 숫자 둘 사이에 혼자 있는 "|", "l", "I", "ㅣ"는 OCR이 수량 "1"을 잘못 읽은 것이다. ("203251 20,526 | 20,526")
_LONE_QUANTITY_RE = re.compile(r"(?<=\d)\s+[|lIㅣ]\s+(?=-?\d)")
# 이름이라면 한글이 한 글자라도 있거나 영문자가 둘 이상 있어야 한다. ("20,526 |", "- 500"은 이름이 아니다)
_WORD_RE = re.compile(r"[가-힣]|[A-Za-z].*[A-Za-z]")
# 용량 표기("500ml", "1.5L", "90g")의 영문자는 이름의 글자로 세지 않는다
_SIZE_RE = re.compile(r"\d[\d.,]*\s*(?:ml|l|g|kg|oz)(?![A-Za-z])", re.IGNORECASE)


def _fix_lookalike_amount(match: re.Match) -> str:
    sign, number, won = match.groups()
    if not re.search(r"\d", number):
        return match.group()
    return sign + number.translate(_DIGIT_LOOKALIKES) + won


# ── 1. 줄 묶기 ───────────────────────────────────────────────────────────


def estimate_skew(boxes: list[OcrBox]) -> float:
    """사진이 기운 각도(라디안). 가로로 긴 글자 상자들의 기울기 중앙값. 1° 미만이면 0으로 본다."""
    angles = [
        box.angle
        for box in boxes
        if abs(box.angle) < MAX_SKEW and (box.x1 - box.x0) > 2 * (box.height or (box.y1 - box.y0))
    ]
    if len(angles) < 3:
        return 0.0
    skew = statistics.median(angles)
    return skew if abs(skew) >= MIN_SKEW else 0.0


def group_lines(boxes: list[OcrBox]) -> list[tuple[str, float]]:
    """박스를 줄 단위로 묶어 (줄 텍스트, 평균 score) 목록으로 돌려준다.

    사진이 기울었으면 그만큼 좌표를 되돌려서 묶는다. 기운 채로 묶으면 오른쪽 끝의 가격이 아래 줄로 넘어간다.
    """
    boxes = [box for box in boxes if box.text and box.text.strip()]
    if not boxes:
        return []
    skew = estimate_skew(boxes)
    cos, sin = math.cos(skew), math.sin(skew)

    def center_y(box: OcrBox) -> float:
        return (box.y0 + box.y1) / 2 * cos - (box.x0 + box.x1) / 2 * sin

    def left_x(box: OcrBox) -> float:
        return box.x0 * cos + (box.y0 + box.y1) / 2 * sin

    tolerance = statistics.median(abs(box.height or (box.y1 - box.y0)) for box in boxes) * 0.5
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
        row.sort(key=left_x)
        text = " ".join(b.text.strip() for b in row)
        lines.append((text, sum(b.score for b in row) / len(row)))
    return lines


# ── 2. 정규화 ────────────────────────────────────────────────────────────


def normalize_line(text: str) -> str:
    text = text.translate(_FULLWIDTH)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(\d)\s*,\s*(\d{3})(?!\d)", r"\1,\2", text)  # "2, 980" → "2,980"
    text = _LOOKALIKE_AMOUNT_RE.sub(_fix_lookalike_amount, text)  # "1,5OO" → "1,500"
    text = _LONE_QUANTITY_RE.sub(" 1 ", text)  # "20,526 | 20,526" → "20,526 1 20,526"
    text = re.sub(r"-\s+(?=\d)", "-", text)  # "- 500" → "-500"
    # 줄 앞의 순번(001, 01), POS 상품코드와 면세·행사 표시(*, #, ★, (면세), P)
    text = _MARKS_RE.sub("", text)
    text = _SEQUENCE_RE.sub("", text, count=1)
    text = _MARKS_RE.sub("", text)
    text = _PLU_RE.sub("", text, count=1)
    text = _ML_RE.sub("ml", text)
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
    # 단, 끝의 네 개가 (단가, 수량, 할인, 금액)으로 맞아떨어지면 네 개를 모두 숫자로 둔다. ("버거 14,900 1 0 14,900")
    if name_tokens and len(numeric) > 3:
        keep = 4 if len(numeric) >= 4 and _is_four_columns([value for _, value in numeric[-4:]]) else 3
        name_tokens += [token for token, _ in numeric[:-keep]]
        numeric = numeric[-keep:]
    return _Line(" ".join(name_tokens), [value for _, value in numeric], barcode)


def _is_four_columns(numbers: list[int]) -> bool:
    """(단가, 수량, 할인, 금액) 칸이 있는 영수증의 끝 네 숫자: 단가 × 수량 − 할인 = 금액."""
    unit, quantity, discount, amount = numbers
    return unit > 0 and quantity > 0 and discount >= 0 and unit * quantity - discount == amount


def _assign_numbers(numbers: list[int], waiting_quantity: int | None = None) -> tuple[float, int | None, int]:
    """숫자들을 (수량, 단가, 금액)으로 배정한다."""
    if len(numbers) >= 4 and _is_four_columns(numbers[-4:]):
        return float(numbers[-3]), numbers[-4], numbers[-1]
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
            return float(unit), (quantity or None), amount  # 0은 단가가 아니다 (할인 칸의 0을 단가로 읽지 않는다)
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


def _is_wordlike(name: str) -> bool:
    """상품 이름처럼 보이는가: 한글이 있거나 영문자가 둘 이상 있다. 숫자·기호·용량 단위뿐인 줄은 상품이 아니다."""
    return bool(_WORD_RE.search(_SIZE_RE.sub(" ", name)))


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
        if amount < MIN_ITEM_AMOUNT:
            return
        items.append(
            ParsedItem(len(items) + 1, fix_name(name), quantity, unit_price, amount, 0, barcode, round(confidence, 3))
        )

    for text, confidence in lines:
        if not text:
            continue
        if NOISE_RE.search(text) or ADDRESS_RE.search(text) or META_RE.search(text) or DATE_RE.search(text):
            waiting = None
            continue

        line = _split_line(text)
        named = len(line.name) > 1 and _is_wordlike(line.name) and not _is_header_only(line.name)

        if not line.numbers:
            if named:  # (b) 상품명만 → 다음 줄의 숫자를 기다린다
                waiting = _Waiting(line.name, line.barcode, confidence, is_discount=bool(DISCOUNT_RE.search(line.name)))
            elif line.barcode and waiting:
                waiting.barcode = line.barcode
            else:
                waiting = None
            continue

        # 2줄형에서 OCR이 수량 "1"을 "-"로 읽은 경우: 상품명 다음 줄 "8801097235212 1,950 -1,950".
        # 할인 줄은 할인 낱말이 있는 이름 뒤에 오므로, 보통 상품명 뒤의 줄은 부호를 떼면 맞아떨어지는 품목이다.
        if not named and waiting and not waiting.is_discount and line.numbers[-1] < 0:
            values = [abs(number) for number in line.numbers]
            if len(values) >= 2 and (values[-1] == values[-2] or _is_triplet(values)):
                line.numbers = values

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
        # "매장명:(주)행복주점-역삼점 [영수증]"처럼 이름표가 있으면 그 뒤가 매장명이다
        if (match := STORE_LABEL_RE.search(line)) and (name := RECEIPT_WORD_RE.sub("", match.group(1)).strip()):
            return name
    for line in lines[:15]:
        if (match := STORE_BIZ_RE.match(line)) and len(HANGUL_RE.findall(match.group(1))) >= 2:
            return match.group(1).strip()
    for line in top:
        if STORE_RE.search(line) and not META_RE.search(line):
            return line
    for line in top:
        if (
            len(HANGUL_RE.findall(line)) >= 2
            and not NOISE_RE.search(line)
            and not ADDRESS_RE.search(line)
            and "번호" not in line  # 주문번호·대기번호
            and not _is_header_only(line)
        ):
            # "가게이름/사업자번호/대표자"처럼 한 줄에 찍히면 가게 이름만
            return STORE_TAIL_RE.sub("", line) or line
    return None


def find_purchase_date(lines: list[str]) -> date | None:
    for pattern in (DATE_RE, COMPACT_DATE_RE):
        for line in lines:
            for year, month, day in pattern.findall(line):
                try:
                    return date(int(year), int(month), int(day))
                except ValueError:
                    continue  # 13월 45일 같은 잘못된 날짜는 무시
    for line in lines:
        if DATE_LABEL_RE.search(line):
            for year, month, day in SHORT_DATE_RE.findall(line):
                try:
                    return date(2000 + int(year), int(month), int(day))
                except ValueError:
                    continue
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
    from app.services.order_screen import is_order_screen, parse_order_screen  # 순환 import를 피하려고 함수 안에서

    raw = group_lines(boxes)
    # 쇼핑몰 주문내역 화면은 영수증과 모양이 전혀 달라서 따로 읽는다. (normalize_line은 영수증 줄 전용이다)
    if is_order_screen([text for text, _ in raw]):
        return parse_order_screen(raw)
    grouped = [(normalize_line(text), confidence) for text, confidence in raw]
    lines = [text for text, _ in grouped]
    start, end = find_item_section(lines)
    return ParsedReceipt(
        store_name=find_store_name(lines),
        purchased_on=find_purchase_date(lines),
        total_amount=find_total(lines),
        items=parse_items(grouped[start:end]),
        lines=lines,
    )
