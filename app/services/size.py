"""상품명에서 용량·중량·개수(규격) 표기를 뽑아 정리한다. 순수 함수.

    "서울우유 1L"        → 1L      (부피 1000ml)
    "칠성사이다 355ml 6캔" → 355ml×6 (부피 2130ml)
    "삼겹살 600g"        → 600g    (무게 600g)
    "물티슈 100매"       → 100매   (개수 100)
"""

import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Size:
    text: str  # 정리한 표기: "1L", "200ml×6", "100매"
    kind: str  # volume / weight / count
    amount: float  # 총량. 부피는 ml, 무게는 g, 개수는 그 수
    unit: str  # "ml" / "g" / 세는 말("입", "매", "구" …)


# 표기 → (종류, 기본 단위, 기본 단위로의 배수, 정리해서 보여 줄 표기)
_MEASURES: dict[str, tuple[str, str, float, str]] = {
    "ml": ("volume", "ml", 1, "ml"),
    "밀리리터": ("volume", "ml", 1, "ml"),
    "cc": ("volume", "ml", 1, "cc"),
    "l": ("volume", "ml", 1000, "L"),
    "리터": ("volume", "ml", 1000, "L"),
    "mg": ("weight", "g", 0.001, "mg"),
    "g": ("weight", "g", 1, "g"),
    "그램": ("weight", "g", 1, "g"),
    "kg": ("weight", "g", 1000, "kg"),
    "킬로그램": ("weight", "g", 1000, "kg"),
    "킬로": ("weight", "g", 1000, "kg"),
    "키로": ("weight", "g", 1000, "kg"),
}

# 세는 말 → 정리한 표기
_COUNTERS: dict[str, str] = {
    "개입": "입", "매입": "매", "입": "입", "개": "개", "구": "구", "매": "매", "장": "장", "팩": "팩",
    "봉지": "봉", "봉": "봉", "병": "병", "캔": "캔", "롤": "롤", "포기": "포기", "포": "포", "정": "정",
    "알": "알", "통": "통", "모": "모", "마리": "마리", "단": "단", "송이": "송이", "줄": "줄",
    "인분": "인분", "박스": "박스", "pcs": "개", "ea": "개", "p": "개", "t": "T",
}  # fmt: skip
# 용량과 같이 적혀 있어도 "묶음 수"가 아닌 것들 ("닭 1마리 1kg", "삼겹살 600g 2인분")
_NOT_PACK = {"구", "통", "모", "마리", "단", "송이", "줄", "포기", "인분", "박스"}

# 영수증에 섞여 나오는 한 글자짜리 단위 기호
_SYMBOLS = str.maketrans({"㎖": "ml", "ℓ": "l", "㎏": "kg", "㎎": "mg", "㏄": "cc", "×": "x", "＊": "*"})


def _alternation(words: dict) -> str:
    return "|".join(sorted(words, key=len, reverse=True))  # 긴 것부터: "ml"이 "l"보다, "개입"이 "개"보다 먼저


_NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
# 단위 뒤에 글자가 이어지면 단위가 아니다: "100g당"(단가 표기), "3개월"
_UNIT_END = r"(?=$|[^A-Za-z가-힣]|x\s*\d)"
_MEASURE_RE = re.compile(rf"(?<![\d.,]){_NUMBER}\s*({_alternation(_MEASURES)}){_UNIT_END}", re.IGNORECASE)
_COUNT_RE = re.compile(rf"(?<![\d.,])(\d{{1,4}})\s*({_alternation(_COUNTERS)}){_UNIT_END}", re.IGNORECASE)
_TIMES_AFTER_RE = re.compile(r"\s*[x*]\s*(\d{1,3})(?!\d)", re.IGNORECASE)  # "200ml*6", "200ml x 24입"
_TIMES_BEFORE_RE = re.compile(r"(?<!\d)(\d{1,3})\s*[x*]\s*$", re.IGNORECASE)  # "6x200ml"


def _plain(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def _pack_count(text: str, measure: re.Match) -> int:
    """용량 옆에 적힌 묶음 수. 없으면 1."""
    after = _TIMES_AFTER_RE.match(text, measure.end())
    if after:
        return max(1, int(after.group(1)))
    before = _TIMES_BEFORE_RE.search(text[: measure.start()])
    if before:
        return max(1, int(before.group(1)))
    for count in _COUNT_RE.finditer(text):
        outside = count.start() >= measure.end() or count.end() <= measure.start()
        if outside and _COUNTERS[count.group(2).lower()] not in _NOT_PACK:
            return max(1, int(count.group(1)))
    return 1


@lru_cache(maxsize=4096)
def extract_size(name: str | None) -> Size | None:
    """상품명에 용량·중량·개수 표기가 있으면 정리해서 돌려준다. 없으면 None.

    부피·무게가 있으면 그것을 쓰고(묶음 수가 있으면 곱한다), 없으면 개수 표기를 쓴다.
    """
    text = (name or "").translate(_SYMBOLS)

    measure = _MEASURE_RE.search(text)
    if measure:
        value = float(measure.group(1).replace(",", ""))
        kind, unit, factor, shown = _MEASURES[measure.group(2).lower()]
        if value > 0:
            packs = _pack_count(text, measure)
            label = f"{_plain(value)}{shown}" + (f"×{packs}" if packs > 1 else "")
            return Size(label, kind, round(value * factor * packs, 3), unit)

    count = _COUNT_RE.search(text)
    if count and int(count.group(1)) > 0:
        unit = _COUNTERS[count.group(2).lower()]
        return Size(f"{int(count.group(1))}{unit}", "count", float(count.group(1)), unit)
    return None
