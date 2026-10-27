"""OCR이 자모 하나를 잘못 읽은 상품명 낱말을 아는 낱말로 고친다. ("서울우규" → "서울우유")

확실할 때만 고친다.
- 세 글자 이상인 아는 낱말만 후보로 쓴다. 두 글자는 "오리 → 오이"처럼 멀쩡한 말을 바꾸기 쉽다.
- 한글 덩어리 전체나 그 끝부분만 고친다. 한국어 상품명은 핵심 명사가 끝에 온다. ("마스카포네"가 "마스크"가 되지 않게)
- 초성·중성·종성 중 하나만 다르고, 그렇게 고친 결과가 하나뿐일 때만 고친다. ("소서지"는 소시지·소세지 둘 다라서 그대로)
- 덩어리 안에 이미 아는 낱말(두 글자 키워드 포함)이 있으면 OCR이 제대로 읽은 것으로 보고 손대지 않는다.
"""

import re

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
)

_KNOWN = {word for category in CATEGORIES.values() for word in category.keywords if len(word) >= 2}
_KNOWN.update(EXTRA_WORDS)
_TARGETS = sorted(word for word in _KNOWN if len(word) >= 3 and re.fullmatch(r"[가-힣]+", word))
_RUN_RE = re.compile(r"[가-힣]{3,}")


def _jamo(syllable: str) -> tuple[int, int, int]:
    code = ord(syllable) - 0xAC00
    return code // 588, code // 28 % 21, code % 28


def _distance(a: str, b: str) -> int:
    """글자 수가 같은 두 한글 낱말에서 다른 초성·중성·종성의 개수."""
    return sum(x != y for left, right in zip(a, b) for x, y in zip(_jamo(left), _jamo(right)))


def _fix_run(run: str) -> str:
    if any(word in run for word in _KNOWN):
        return run
    # 고친 결과로 비교한다: "칠성사이디"는 "칠성사이다"로도 "사이다"로도 같은 결과가 된다.
    fixed = {
        run[: -len(word)] + word for word in _TARGETS if len(word) <= len(run) and _distance(run[-len(word):], word) == 1
    }
    return fixed.pop() if len(fixed) == 1 else run


def fix_name(name: str) -> str:
    return _RUN_RE.sub(lambda match: _fix_run(match.group()), name)
