"""상품명 → 카테고리 분류, 소비기한 추정.

일수는 "구매일 기준의 보수적인 추정치"다. 고치고 싶으면 아래 CATEGORIES 사전만 손보면 된다.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol


@dataclass(frozen=True)
class Category:
    label: str
    storage: str | None  # fridge / freezer / room
    days: int | None
    keywords: tuple[str, ...] = ()


NON_FOOD = "non_food"
FROZEN = "frozen"
OTHER = "other"

# 키워드 규칙
# - 위험한 1글자 키워드는 넣지 않는다: 국, 탕(설탕·국수 오매칭), 차, 물, 란, 회, 파, 봉, 번
# - 짧은 키워드가 다른 상품에 걸리면, 그 상품 쪽에 더 긴 키워드를 추가해서 이기게 만든다.
#   (예: "무" ← 단무지·쌈무·무화과, "배" ← 배추·알배기, "간장" ← 게장)
CATEGORIES: dict[str, Category] = {
    "milk": Category("우유", "fridge", 10, ("우유", "락토프리", "저지방우유")),
    "yogurt": Category("요거트", "fridge", 14, ("요거트", "요구르트", "요플레", "야쿠르트", "불가리스", "액티비아")),
    "cheese": Category("치즈", "fridge", 30, ("치즈", "모짜렐라")),
    "butter": Category("버터·크림", "fridge", 60, ("버터", "마가린", "크림치즈", "생크림")),
    "egg": Category("달걀", "fridge", 30, ("계란", "달걀", "유정란", "메추리알")),
    "tofu": Category("두부", "fridge", 7, ("두부", "순두부", "연두부", "유부")),
    "beef": Category(
        "소고기", "fridge", 3,
        ("소고기", "쇠고기", "한우", "등심", "안심", "채끝", "차돌", "우삼겹", "불고기", "양지", "사태", "갈비"),
    ),
    "pork": Category(
        "돼지고기", "fridge", 3,
        ("돼지", "돈육", "삼겹", "목살", "앞다리", "뒷다리", "항정", "대패", "돼지갈비", "돼지불고기"),
    ),
    "chicken": Category("닭고기", "fridge", 2, ("닭", "닭가슴살", "닭다리", "생닭", "닭갈비", "닭볶음탕")),
    "minced": Category("다진고기", "fridge", 2, ("다짐육", "다진")),
    "processed_meat": Category(
        "햄·소시지", "fridge", 21, ("햄", "소시지", "소세지", "베이컨", "스팸", "비엔나", "프랑크")
    ),
    "seafood": Category(
        "생선·해산물", "fridge", 2,
        (
            "생선", "고등어", "연어", "갈치", "삼치", "조기", "오징어", "새우", "조개", "바지락", "홍합", "굴",
            "전복", "낙지", "문어", "쭈꾸미", "주꾸미", "꽃게", "게장", "동태", "명태",
        ),
    ),
    "fish_cake": Category("어묵·맛살", "fridge", 14, ("어묵", "오뎅", "맛살", "크래미")),
    "kimchi": Category(
        "김치·절임", "fridge", 30, ("김치", "깍두기", "열무", "겉절이", "단무지", "쌈무", "젓갈", "장아찌", "피클")
    ),
    "side_dish": Category("반찬·샐러드", "fridge", 5, ("반찬", "나물", "무침", "볶음", "조림", "샐러드")),
    "ready_meal": Category(
        "도시락·즉석조리", "fridge", 2,
        (
            "도시락", "김밥", "삼각김밥", "샌드위치", "햄버거", "주먹밥", "초밥", "밀키트", "찌개", "떡볶이",
            "곰탕", "육개장", "갈비탕", "설렁탕", "삼계탕", "감자탕", "해장국", "미역국", "치킨",
        ),
    ),
    "leafy_veg": Category(
        "잎채소·나물류", "fridge", 4,
        (
            "상추", "양상추", "깻잎", "시금치", "배추", "알배기", "봄동", "얼갈이", "청경채", "부추", "콩나물",
            "숙주", "대파", "쪽파", "미나리", "쑥갓", "케일", "로메인",
        ),
    ),
    "vegetable": Category(
        "채소", "fridge", 7,
        (
            "오이", "애호박", "호박", "가지", "파프리카", "피망", "고추", "브로콜리", "토마토", "버섯", "팽이",
            "양배추", "옥수수",
        ),
    ),
    "root_veg": Category(
        "뿌리채소", "room", 21, ("감자", "고구마", "양파", "당근", "무", "마늘", "생강", "단호박", "연근", "우엉")
    ),
    "fruit": Category(
        "과일", "fridge", 7,
        (
            "사과", "배", "귤", "오렌지", "레몬", "자몽", "포도", "샤인머스캣", "키위", "참외", "수박", "멜론",
            "복숭아", "자두", "망고", "파인애플", "아보카도", "단감", "한라봉",
        ),
    ),
    "soft_fruit": Category("무른 과일", "fridge", 4, ("딸기", "바나나", "블루베리", "체리", "무화과")),
    "bread": Category(
        "빵", "room", 4,
        ("빵", "식빵", "베이글", "크루아상", "크로와상", "바게트", "머핀", "케이크", "케익", "카스테라", "도넛"),
    ),
    "rice_cake": Category("떡", "room", 3, ("떡", "가래떡", "인절미", "송편")),
    "frozen": Category(
        "냉동식품", "freezer", 90,
        (
            "냉동", "만두", "아이스크림", "너겟", "동그랑땡", "피자", "돈까스", "돈가스", "핫도그", "볶음밥",
            "감자튀김",
        ),
    ),
    "makgeolli": Category("막걸리", "fridge", 10, ("막걸리",)),
    "drink": Category(
        "음료", "room", 180,
        (
            "주스", "쥬스", "음료", "콜라", "사이다", "탄산수", "커피", "커피믹스", "아메리카노", "라떼", "녹차",
            "홍차", "보리차", "유자차", "아이스티", "생수", "삼다수", "두유", "식혜", "맥주", "소주", "와인",
            "에이드", "즙",
        ),
    ),
    "snack": Category(
        "과자", "room", 180,
        (
            "과자", "스낵", "초콜릿", "초코", "초코파이", "쿠키", "젤리", "사탕", "캔디", "껌", "새우깡", "감자칩",
            "칩", "비스킷", "크래커", "팝콘", "양갱", "시리얼", "그래놀라",
        ),
    ),
    "instant": Category(
        "라면·즉석식품", "room", 180,
        ("라면", "컵라면", "짜파게티", "즉석밥", "햇반", "컵밥", "카레", "짜장", "우동", "스프", "수프", "누룽지"),
    ),
    "canned": Category("통조림", "room", 365, ("통조림", "참치", "꽁치", "골뱅이", "스위트콘", "황도")),
    "sauce": Category(
        "양념·소스", "room", 365,
        (
            "간장", "된장", "고추장", "쌈장", "케첩", "케찹", "마요네즈", "머스타드", "소스", "드레싱", "식초",
            "참기름", "들기름", "식용유", "올리브유", "포도씨유", "카놀라유", "설탕", "소금", "후추", "고춧가루",
            "굴소스", "액젓", "맛술", "물엿", "올리고당", "시럽", "스톡", "다시다", "꿀", "잼",
        ),
    ),
    "grain": Category(
        "곡물·면", "room", 365,
        (
            "쌀", "현미", "흑미", "잡곡", "보리", "귀리", "오트밀", "밀가루", "부침가루", "튀김가루", "빵가루",
            "호떡믹스", "핫케이크믹스", "국수", "소면", "파스타", "스파게티", "당면",
        ),
    ),
    "dried": Category(
        "건어물·견과", "room", 180,
        (
            "견과", "믹스넛", "아몬드", "땅콩", "호두", "캐슈넛", "피스타치오", "건포도", "김", "멸치", "미역",
            "다시마", "황태", "북어", "오징어채", "진미채", "건새우", "쥐포", "육포",
        ),
    ),
    # 비식품 키워드는 위치와 상관없이 이기므로, 공백을 지웠을 때 식품명에 우연히 걸리는 말은 넣지 않는다.
    # (예: 면봉 ← "신라면봉지", 면도 ← "라면도시락", 우산 ← "한우산적", 치실 ← "김치실속", 랩 ← "크랩")
    "non_food": Category(
        "비식품", None, None,
        (
            "휴지", "화장지", "물티슈", "갑티슈", "각티슈", "미용티슈", "키친타올", "키친타월", "타올", "타월",
            "세제", "섬유유연제", "유연제", "표백제", "락스", "샴푸", "린스", "컨디셔너", "바디워시", "핸드워시",
            "로션", "치약", "칫솔", "가글", "비누", "면도기", "면도날", "생리대", "기저귀", "화장솜", "종량제",
            "봉투", "쇼핑백", "장바구니", "쓰레기", "보증금", "지퍼백", "위생백", "위생장갑", "위생랩", "크린랩",
            "호일", "비닐", "고무장갑", "면장갑", "목장갑", "수세미", "행주", "종이컵", "빨대", "젓가락",
            "이쑤시개", "건전지", "배터리", "마스크", "방향제", "탈취제", "살충제", "스프레이", "담배", "라이터",
            "양말", "스타킹",
        ),
    ),
    "other": Category("기타", "fridge", 7),
}

_WHITESPACE = re.compile(r"\s+")


def label_of(category: str | None) -> str:
    found = CATEGORIES.get(category or "")
    return found.label if found else CATEGORIES[OTHER].label


def storage_of(category: str) -> str:
    """카테고리 기본 보관 위치. 비식품처럼 정해진 곳이 없으면 실온."""
    found = CATEGORIES.get(category)
    return (found.storage if found else None) or "room"


def classify(name: str) -> str:
    """상품명으로 카테고리 key를 고른다.

    한국어 상품명은 핵심 명사가 뒤에 오므로 "끝나는 위치가 가장 뒤인 키워드"를 고르고,
    끝 위치가 같으면 더 긴 키워드를 고른다. (딸기우유→우유, 우유식빵→빵, 양파 vs 파→양파)
    """
    text = _WHITESPACE.sub("", name or "").lower()
    if not text:
        return OTHER
    if "냉동" in text:
        return FROZEN
    # 비식품 키워드는 위치와 상관없이 우선한다. ("주방세제 레몬향"이 과일이 되지 않게)
    if any(keyword in text for keyword in CATEGORIES[NON_FOOD].keywords):
        return NON_FOOD

    best: tuple[int, int] | None = None  # (끝 위치, 키워드 길이)
    best_key = OTHER
    for key, category in CATEGORIES.items():
        if key == NON_FOOD:
            continue
        for keyword in category.keywords:
            index = text.rfind(keyword)
            if index < 0:
                continue
            candidate = (index + len(keyword), len(keyword))
            if best is None or candidate > best:
                best, best_key = candidate, key
    return best_key


_UNIT_DAYS = {"년": 365, "개월": 30, "달": 30, "주": 7, "일": 1}
_DURATION = re.compile(r"(\d+)\s*(년|개월|달|주|일)")


def parse_shelf_life_days(text: str | None) -> int | None:
    """C005의 POG_DAYCNT 문구("제조일로부터 12개월")를 일수로 바꾼다.

    `,` `/` `;`로 나눈 조각 중 기간이 처음 나오는 조각만 쓰고, 그 안의 년·개월·주·일을 모두 더한다.
    기간을 읽을 수 없으면("별도표기" 등) None.
    """
    if not text:
        return None
    for part in re.split(r"[,/;]", text):
        days = sum(int(number) * _UNIT_DAYS[unit] for number, unit in _DURATION.findall(part))
        if days > 0:
            return days
    return None


class ProductLike(Protocol):
    """foodsafety.ProductInfo 중 추정에 필요한 부분."""

    name: str | None
    food_type: str | None
    shelf_life_days: int | None


@dataclass(frozen=True)
class Estimate:
    category: str
    category_label: str
    storage: str
    is_food: bool
    expiry_date: date | None
    expiry_source: str | None  # barcode / category, 소비기한이 없으면 None


def estimate(
    name: str,
    purchased_on: date,
    product: ProductLike | None = None,
    *,
    category: str | None = None,
) -> Estimate:
    """카테고리와 소비기한을 추정한다. category를 주면 분류를 건너뛰고 그 값을 쓴다."""
    key = category if category in CATEGORIES else classify(name)
    if category is None and key == OTHER and product is not None:
        # 영수증 이름으로 못 맞히면 바코드로 찾은 제품명 → 식품유형 순으로 다시 분류해 본다.
        for hint in (product.name, product.food_type):
            if hint and (key := classify(hint)) != OTHER:
                break

    info = CATEGORIES[key]
    if key == NON_FOOD:
        return Estimate(key, info.label, storage_of(key), False, None, None)

    shelf_life_days = product.shelf_life_days if product is not None else None
    if shelf_life_days:
        # POG_DAYCNT는 제조일 기준이라 실제보다 길 수 있다 → 앱에서 "포장지 확인" 안내용으로 source를 남긴다.
        return Estimate(
            key, info.label, storage_of(key), True, purchased_on + timedelta(days=shelf_life_days), "barcode"
        )
    return Estimate(key, info.label, storage_of(key), True, purchased_on + timedelta(days=info.days or 0), "category")
