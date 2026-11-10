"""OCR이 자모 하나를 잘못 읽은 상품명 낱말을 아는 낱말로 고친다. ("서울우규" → "서울우유")

확실할 때만 고친다.
- 세 글자 이상인 아는 낱말은 초성·중성·종성 중 아무거나 하나가 다르면 후보가 된다.
- 두 글자 낱말은 "오리 → 오이"처럼 멀쩡한 말을 바꾸기 쉬워서, OCR이 영수증에서 실제로 자주 틀리는 경우만 고친다:
  된소리와 예사소리(삼장 → 쌈장), 받침이 빠지거나 붙은 것(깨잎 → 깻잎).
- 한글 덩어리 전체나 그 끝부분만 고친다. 한국어 상품명은 핵심 명사가 끝에 온다. ("마스카포네"가 "마스크"가 되지 않게)
- 초성·중성·종성 중 하나만 다르고, 그렇게 고친 결과가 하나뿐일 때만 고친다. ("소서지"는 소시지·소세지 둘 다라서 그대로)
- 덩어리가 아는 낱말로 끝나면(핵심 명사를 제대로 읽음) 손대지 않는다. 앞쪽에만 아는 낱말이 있으면("새우강") 고칠 수 있지만,
  고친 결과에도 그 낱말이 그대로 남아야 하고, OCR이 흔히 틀리는 차이(된소리·예사소리, 모음, 받침)만 고친다
  ("새우강" → "새우깡", 하지만 아는 낱말이 아닌 "새우탕"을 "새우깡"으로 바꾸지는 않는다).
- 그 줄의 OCR 신뢰도가 낮으면(NAME_UNSURE_BELOW 미만) 세 글자 낱말도 한 글자가 통째로 달라진 것까지 고친다. 신뢰도가 높으면 위 규칙 그대로다.
"""

import re
from pathlib import Path

from app.config import settings
from app.services.expiry import CATEGORIES

# 카테고리 키워드 말고도 영수증에 자주 나오는 세 글자 이상 낱말 (상품명·브랜드). 고칠 대상으로만 쓴다.
EXTRA_WORDS = (
    "서울우유", "매일우유", "남양우유", "바나나우유", "초코우유", "딸기우유", "커피우유", "멸균우유",
    "풀무원", "비비고", "오뚜기", "신라면", "진라면", "안성탕면", "너구리", "불닭볶음면",
    "동원참치", "사조참치", "삼겹살", "오겹살", "방울토마토", "대추토마토", "양송이", "새송이",
    "표고버섯", "팽이버섯", "느타리", "청양고추", "꽈리고추", "깐마늘", "다진마늘", "부침두부", "찌개두부",
    "숙주나물", "오렌지주스", "사과주스", "포도주스", "제로콜라", "코카콜라", "펩시콜라", "칠성사이다",
    "포카리스웨트", "게토레이", "아이시스", "백산수", "참이슬", "처음처럼", "포카칩", "꼬깔콘", "허니버터칩",
    "빼빼로", "맛동산", "홈런볼", "프링글스", "바나나킥", "양파링", "메로나", "월드콘", "투게더",
    # 2026-11-10: more grocery products and brands (OCR typo targets only)
    "바나나맛우유", "새우깡", "감자깡", "양파깡", "고래밥", "초코파이", "오예스", "몽쉘", "카스타드", "마가렛트", "버터링",
    "다이제", "참크래커", "꼬북칩", "오징어집", "꿀꽈배기", "자갈치", "썬칩", "칙촉", "에이스", "하리보", "마이쮸",
    "새콤달콤", "자일리톨", "빵빠레", "부라보콘", "붕어싸만코", "죠스바", "수박바", "스크류바", "비비빅", "누가바",
    "돼지바", "쌍쌍바", "하겐다즈", "왕교자", "물만두", "짜파게티", "비빔면", "육개장", "참깨라면", "사리곰탕",
    "열라면", "진짬뽕", "짜왕", "새우탕", "큰사발", "사발면", "컵누들", "양반김", "광천김", "재래김", "도시락김", "참치캔", "리챔", "목우촌",
    "청정원", "종가집", "오리온", "크라운", "빙그레", "파스퇴르", "상하목장", "요플레", "액티비아",
    "불가리스", "야쿠르트", "아침햇살", "박카스", "칸타타", "레쓰비", "조지아", "바리스타", "맥스웰",
    "떡볶이", "어묵", "물티슈", "키친타월", "두루마리", "주방세제", "섬유유연제",
)

# Common ingredient words from the recipe database (scripts/build_food_words.py).
FOOD_WORDS = tuple(
    line for line in (Path(__file__).with_name("food_words.txt").read_text(encoding="utf-8").splitlines()) if line and not line.startswith("#")
)

_KNOWN = {word for category in CATEGORIES.values() for word in category.keywords if len(word) >= 2}
_KNOWN.update(EXTRA_WORDS)
_KNOWN.update(FOOD_WORDS)
_TARGETS = sorted(word for word in _KNOWN if re.fullmatch(r"[가-힣]{2,}", word))
_RUN_RE = re.compile(r"[가-힣]{2,}")
# 초성 번호로 된소리와 예사소리: ㄱ-ㄲ, ㄷ-ㄸ, ㅂ-ㅃ, ㅅ-ㅆ, ㅈ-ㅉ
_TENSE_PAIRS = {frozenset(pair) for pair in ((0, 1), (3, 4), (7, 8), (9, 10), (12, 13))}


def _jamo(syllable: str) -> tuple[int, int, int]:
    code = ord(syllable) - 0xAC00
    return code // 588, code // 28 % 21, code % 28


def _close(seen: str, word: str, strict: bool = False) -> bool:
    """글자 수가 같은 두 낱말에서 초성·중성·종성이 하나만 다르다. 두 글자 낱말은 위의 두 경우만.

    strict면 초성은 된소리·예사소리 차이만 같은 것으로 본다 (ㅌ↔ㄲ 같은 차이는 다른 낱말일 가능성이 크다).
    """
    differences = [
        (slot, x, y)
        for left, right in zip(seen, word)
        for slot, (x, y) in enumerate(zip(_jamo(left), _jamo(right)))
        if x != y
    ]
    if len(differences) != 1:
        return False
    slot, x, y = differences[0]
    if strict and slot == 0 and frozenset((x, y)) not in _TENSE_PAIRS:
        return False
    if len(word) >= 3:
        return True
    return (slot == 0 and frozenset((x, y)) in _TENSE_PAIRS) or (slot == 2 and 0 in (x, y))


def _one_syllable_off(seen: str, word: str) -> bool:
    """글자 수가 같고 한 글자만 다르다. 그 글자도 초성·중성·종성 중 하나는 같아야 한다. ("코카율라" → "코카콜라": 받침 ㄹ)"""
    wrong = [(x, y) for x, y in zip(seen, word) if x != y]
    return len(wrong) == 1 and any(a == b for a, b in zip(_jamo(wrong[0][0]), _jamo(wrong[0][1])))


def _fix_run(run: str, loose: bool) -> str:
    inside = [word for word in _KNOWN if word in run]
    if any(run.endswith(word) for word in inside):
        return run
    # 고친 결과로 비교한다: "칠성사이디"는 "칠성사이다"로도 "사이다"로도 같은 결과가 된다.
    # 네 글자 이상 낱말은 나머지 글자가 모두 맞으면 한 글자가 통째로 달라도(자모 둘 이상) 고친다. 우연히 맞을 가능성이 낮다.
    # 줄을 흐리게 읽었을 때(loose)는 세 글자 낱말도 그렇게 고친다.
    shortest = 3 if loose else 4
    strict = bool(inside)  # part of the name was read right: fix only the usual OCR confusions
    fixed = {
        run[: -len(word)] + word
        for word in _TARGETS
        if len(word) <= len(run)
        and (
            _close(run[-len(word):], word, strict)
            or (not strict and len(word) >= shortest and _one_syllable_off(run[-len(word):], word))
        )
    }
    fixed = {name for name in fixed if all(word in name for word in inside)}  # keep the words that were read right
    return fixed.pop() if len(fixed) == 1 else run


def is_blurry(confidence: float | None) -> bool:
    """줄의 OCR 신뢰도가 낮다. 신뢰도를 모르면(None) 낮다고 보지 않는다."""
    return confidence is not None and confidence < settings.name_unsure_below


def fix_name(name: str, confidence: float | None = None) -> str:
    loose = is_blurry(confidence)
    return _RUN_RE.sub(lambda match: _fix_run(match.group(), loose), name)


def is_recognized(name: str) -> bool:
    """이름에 아는 낱말(카테고리 키워드, 자주 나오는 상품명)이 하나라도 들어 있다."""
    return any(word in name for word in _KNOWN)
