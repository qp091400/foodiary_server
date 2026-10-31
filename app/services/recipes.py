"""Versioned public recipe catalogue. No network access in recommendation requests."""

import json
import random
import re
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.services.recipe_names import display_name, dish_family

SOURCE_URL = "https://www.foodsafetykorea.go.kr/api/openApiInfo.do?svc_no=COOKRCP01"
SOURCE = "식품의약품안전처 · 조리식품의 레시피 DB"

# Specific ingredients remain specific: 연두부 != 두부, 무염버터 != 버터.
# Only spelling/synonym equivalence, not automatic ingredient substitutions.
ALIASES = {
    "달걀": ("달걀", "계란"), "돼지고기": ("돼지고기", "돈육"),
    "소고기": ("소고기", "쇠고기", "우육", "한우"),
    "삼겹살": ("삼겹살", "삼겹"), "목살": ("목살",),
    "닭가슴살": ("닭가슴살", "닭 가슴살"), "닭고기": ("닭고기", "닭"),
    "대파": ("대파",), "쪽파": ("쪽파",), "양파": ("양파",),
    "마늘": ("마늘", "다진마늘"), "생강": ("생강",),
    "애호박": ("애호박",), "시금치": ("시금치",), "당근": ("당근",),
    "감자": ("감자",), "고구마": ("고구마",), "양배추": ("양배추",),
    "배추": ("배추",), "김치": ("김치", "배추김치", "신김치"),
    "두부": ("두부",), "연두부": ("연두부",), "순두부": ("순두부",),
    "두부면": ("두부면",), "포두부": ("포두부",), "열무김치": ("열무김치",),
    "백김치": ("백김치",), "갓김치": ("갓김치",), "오이김치": ("오이김치",),
    "콩나물": ("콩나물",), "숙주": ("숙주", "숙주나물"),
    "표고버섯": ("표고버섯", "표고"), "느타리버섯": ("느타리버섯", "느타리"),
    "팽이버섯": ("팽이버섯", "팽이"), "양송이버섯": ("양송이버섯", "양송이"),
    "우유": ("우유",), "생크림": ("생크림",), "치즈": ("치즈",),
    "무염버터": ("무염버터",), "버터": ("버터",), "요거트": ("요거트", "요구르트"),
    "칵테일새우": ("칵테일새우",), "새우": ("새우",), "고등어": ("고등어",),
    "홍합": ("홍합", "홍합살"), "참치": ("참치",), "오징어": ("오징어",),
    "오이": ("오이",), "파프리카": ("파프리카",), "피망": ("피망",),
    "청양고추": ("청양고추",), "홍고추": ("홍고추",), "풋고추": ("풋고추",),
    "상추": ("상추",), "깻잎": ("깻잎",), "쌀": ("쌀",), "밥": ("밥", "쌀밥"),
    "베이컨": ("베이컨",), "햄": ("햄",), "소금": ("소금",), "설탕": ("설탕",),
    "간장": ("간장",), "저염간장": ("저염간장",), "고추장": ("고추장",), "된장": ("된장",), "쌈장": ("쌈장",),
    "참기름": ("참기름",), "들기름": ("들기름",), "식용유": ("식용유",),
    "올리브유": ("올리브유", "올리브오일"), "후추": ("후추", "후춧가루"),
    "고춧가루": ("고춧가루", "고추가루"), "식초": ("식초",), "깨": ("깨", "참깨", "깨소금", "통깨"),
    "부추": ("부추", "조선부추"), "땅콩": ("땅콩",),
    "밀가루": ("밀가루",), "전분": ("전분",), "물": ("물",),
}
# Extend recognition to packaged foods and produce used across the larger sources.
ALIASES.update({
    '토마토': ('토마토',), '방울토마토': ('방울토마토', '대추방울토마토'),
    '브로콜리': ('브로콜리',), '아보카도': ('아보카도',), '레몬': ('레몬',),
    '사과': ('사과',), '바나나': ('바나나',), '딸기': ('딸기',), '블루베리': ('블루베리',),
    '망고': ('망고',), '키위': ('키위', '참다래'), '복숭아': ('복숭아', '천도복숭아'),
    '파인애플': ('파인애플',), '오렌지': ('오렌지',), '체리': ('체리',),
    '메추리알': ('메추리알',), '두유': ('두유',), '연유': ('연유',),
    '모짜렐라치즈': ('모짜렐라', '모차렐라'), '크림치즈': ('크림치즈',),
    '소시지': ('소시지', '소세지', '프랑크소시지'), '스팸': ('스팸', '런천미트'),
    '참치통조림': ('참치통조림', '참치캔', '캔참치'), '맛살': ('맛살', '크래미'),
    '어묵': ('어묵', '오뎅'), '만두': ('만두', '교자'), '식빵': ('식빵',),
    '떡국떡': ('떡국떡',), '떡볶이떡': ('떡볶이떡',), '가래떡': ('가래떡',),
    '스파게티면': ('스파게티면',), '소면': ('소면',), '당면': ('당면',),
    '쌀국수': ('쌀국수',), '우동면': ('우동면', '우동사리'), '라면': ('라면',),
    '김': ('김', '김밥용김', '김밥김'), '미역': ('미역',), '다시마': ('다시마',),
    '연어': ('연어',), '꽃게': ('꽃게',), '문어': ('문어',), '낙지': ('낙지',),
    '바지락': ('바지락',), '멸치': ('멸치',), '명란': ('명란', '명란젓'),
    '완두콩': ('완두콩',), '강낭콩': ('강낭콩',), '병아리콩': ('병아리콩',),
    '아몬드': ('아몬드',), '호두': ('호두',), '캐슈넛': ('캐슈넛',), '옥수수': ('옥수수',),
    '꿀': ('꿀', '벌꿀'), '케첩': ('케첩', '케찹'), '마요네즈': ('마요네즈',),
    '굴소스': ('굴소스',), '돈가스소스': ('돈가스소스', '돈까스소스'),
    '토마토소스': ('토마토소스',), '맛술': ('맛술', '미림'), '올리고당': ('올리고당',),
    '물엿': ('물엿',), '부침가루': ('부침가루',), '튀김가루': ('튀김가루',),
})
STAPLES = {"물", "소금", "설탕", "간장", "저염간장", "고추장", "된장", "참기름", "들기름", "식용유", "올리브유", "후추", "고춧가루", "식초", "깨"}
PARENTS = {"삼겹살": "돼지고기", "목살": "돼지고기", "닭가슴살": "닭고기", '방울토마토': '토마토'}
SUPPLIES = {'칼', '도마', '가위', '비닐봉지', '키친타올', '키친타월', '랩', '밀폐용기',
            '전자레인지', '냄비', '후라이팬', '프라이팬', '없음', '없어요', '재료없음', '준비물',
            '종이호일', '쿠킹호일', '은박지', '이쑤시개', '에어프라이어', '오븐', '텀블러'}
COMBINATION_EXTRAS = {'얼음', '각얼음', '왕각얼음', '얼음물', '찬물', '냉수', '뜨거운물',
                      '끓는물', '따뜻한물', '미지근한물', '정수물', '정수', '생수', '맹물',
                      '수돗물', '쌀뜨물', '베이킹소다', '식소다', '베이킹파우더',
                      '카놀라유', '포도씨유', '해바라기유', '고추기름', '파기름', '콩기름',
                      '물엿', '올리고당', '꿀'}
COMBINATION_EXTRAS.update({'기름장', '매실액', '매실액기스', '매실청', '맛술', '청주', '소주', '천일염', '와사비'})
TOOL_DESCRIPTION = re.compile(r'냄비|프라이팬|후라이팬|전자레인지|전자렌지|에어프라이어|'
                              r'종이호일|종일호일|쿠킹호일|키친타올|키친타월|비닐봉지|오븐팬|텀블러')
WATER_OR_ICE = re.compile(
    r'^(?:물|찬물|뜨거운물|따뜻한물|미지근한물|끓는물|쌀뜨물|생수|얼음물|각얼음|왕각얼음|얼음)'
    r'(?:넉넉|잠길|가득|자작|충분|적당|살짝|많이|약간|조금|취향|기호|필요|재료가|재료를|'
    r'계란이|달걀이|종이컵|소주컵|머그컵|소주잔|한컵|반컵|한잔|또는|이나|or|약|[abc]$)')
COMBINATION_IGNORED = STAPLES | SUPPLIES | COMBINATION_EXTRAS


def is_combination_ingredient(key):
    return bool(key and key not in COMBINATION_IGNORED
                and not WATER_OR_ICE.search(key) and not TOOL_DESCRIPTION.search(key))


def combination_keys(recipe):
    """Distinct food ingredients; quantities, water, basic seasoning and tools do not count."""
    return {p['key'] for p in recipe['ingredients'] if is_combination_ingredient(p['key'])}


def compact(value: str) -> str:
    return re.sub(r"[^가-힣a-z]", "", value.lower())


ALIAS_LOOKUP = sorted([(compact(alias), key) for key, aliases in ALIASES.items()
                       for alias in aliases], key=lambda pair: len(pair[0]), reverse=True)


@lru_cache(maxsize=100000)
def ingredient_key(value: str) -> str:
    name = compact(value)
    name = re.sub(r"^(재료|주재료|양념|육수|고명)", "", name)
    if name.startswith(("말린", "마른", "건표고", "건새우")) or "마른것" in name:
        dried = re.sub(r"^(말린|마른|건)|마른것", "", name)
        return "건조:" + ingredient_key(dried)
    if any(word in name for word in ("돼지고기", "돈육", "소고기", "쇠고기", "한우")):
        animal = "돼지" if "돼지고기" in name or "돈육" in name else "소"
        for cut in ("안심", "등심", "앞다리", "뒷다리", "갈비", "사태", "양지"):
            if cut in name:
                return animal + cut
    if "닭" in name and "다리" in name:
        return "닭다리"
    if "적양배추" in name:
        return "적양배추"
    if "적양파" in name:
        return "적양파"
    for alias, key in ALIAS_LOOKUP:
        if len(alias) == 1:
            if name == alias:
                return key
        elif alias in name:
            # Do not treat manufactured dishes/sauces as their raw ingredient.
            suffix = name.split(alias, 1)[1]
            if any(word in suffix for word in ("소스", "드레싱", "찌개", "볶음", "라면", "과자", "맛", "주스", "육수")):
                continue
            return key
    return name


def ingredient_parts(raw: str) -> list[dict]:
    # Split commas outside quantity parentheses. Keep original quantities for display.
    raw = raw.replace('\x07', ' ')
    raw = re.sub(r'\[[^\]]*\]', '\n', raw)
    parts = re.split(r",(?![^()]*\))|\n|[;；|]", raw)
    result, seen = [], set()
    for part in parts:
        part = re.sub(r"\[[^\]]*\]", "", part).strip().lstrip("•●·- ")
        if not part or len(part) > 240:
            continue
        if not re.search(r"\d|약간|적당|조금", part) and len(parts) > 1:
            if part.endswith(("소스", "양념", "고명", "재료", "찜", "국", "찌개", "볶음", "무침")):
                continue
        part = re.sub(r"^.*?[:：]\s*", "", part)
        name = re.split(r"\d|\(|약간|적당|조금", part, maxsplit=1)[0].strip()
        # Parentheses may specify a required meat cut, not just the quantity.
        key = ingredient_key(part if any(meat in name for meat in ("돼지고기", "돈육", "소고기", "쇠고기", "닭고기", "한우")) else name)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append({"name": name, "text": part, "key": key, "staple": key in STAPLES})
    return result


def normalize(row: dict) -> dict | None:
    steps = [re.sub(r"^\s*\d+[.)]\s*", "", str(row.get(f"MANUAL{i:02}", "")).strip())
             for i in range(1, 21) if str(row.get(f"MANUAL{i:02}", "")).strip()]
    ingredients = ingredient_parts(row.get("RCP_PARTS_DTLS", ""))
    if not row.get("RCP_SEQ") or not row.get("RCP_NM") or not steps or not ingredients:
        return None
    return {"id": str(row["RCP_SEQ"]), "name": row["RCP_NM"],
            "category": row.get("RCP_PAT2", ""), "method": row.get("RCP_WAY2", ""),
            "ingredients": ingredients, "ingredients_text": row.get("RCP_PARTS_DTLS", ""),
            "steps": steps, "tip": row.get("RCP_NA_TIP", ""),
            "source": SOURCE, "source_url": SOURCE_URL}


@lru_cache(maxsize=2)
def _read_catalog(path: str, modified: int) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def catalogue() -> dict:
    path = Path(settings.recipe_data_dir) / "catalogue.json"
    try:
        return _read_catalog(str(path), path.stat().st_mtime_ns)
    except FileNotFoundError:
        return {"recipes": [], "sample": False, "source_total": 0, "imported_at": None}


def rank(recipes: list[dict], foods: list, limit: int = 30, seed: int | None = None) -> list[dict]:
    if limit <= 0:
        return []
    available = set()
    required = []
    for food in foods:
        key = ingredient_key(food.name)
        alternatives = {key}
        if key in PARENTS:
            alternatives.add(PARENTS[key])
        required.append(alternatives)
        available.update(alternatives)
    scored = []
    for recipe in recipes:
        if len(combination_keys(recipe)) < 2:
            continue  # Hard policy, including legacy catalogues and manual overrides.
        recipe_keys = {part["key"] for part in recipe["ingredients"]}
        if not required or any(not (options & recipe_keys) for options in required):
            continue  # Every selected ingredient must occur in this one recipe.
        matched, missing = [], []
        for part in recipe["ingredients"]:
            (matched if part["key"] in available else missing).append(part)
        main_matches = sum(not part["staple"] for part in matched)
        if not main_matches:
            continue  # Salt/water alone should not recommend the entire catalogue.
        main_missing = sum(not part["staple"] for part in missing)
        score = len(missing)
        summary = {key: recipe[key] for key in ("id", "name", "category", "method")}
        summary['display_name'] = display_name(recipe)
        summary.update({key: recipe.get(key, '') for key in ('source', 'cooking_time', 'difficulty', 'servings')})
        summary['has_steps'] = bool(recipe.get('steps'))
        summary.update(matched=[p["text"] for p in matched], missing=[p["text"] for p in missing],
                       missing_main=main_missing)
        scored.append((score, summary, dish_family(recipe)))
    # Shuffle before stable sorting and limiting: equal-rank recipes, including
    # those beyond the first 30, have an equal chance on every request.
    (random if seed is None else random.Random(seed)).shuffle(scored)
    scored.sort(key=lambda row: row[0])
    result, seen = [], set()
    for _, summary, family in scored:
        if family not in seen:
            seen.add(family)
            result.append(summary)
            if len(result) == limit:
                break
    return result
