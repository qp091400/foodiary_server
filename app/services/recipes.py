"""Versioned public recipe catalogue. No network access in recommendation requests."""

import json
import random
import re
from functools import lru_cache
from pathlib import Path

from app.config import settings

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
STAPLES = {"물", "소금", "설탕", "간장", "저염간장", "고추장", "된장", "참기름", "들기름", "식용유", "올리브유", "후추", "고춧가루", "식초", "깨"}
PARENTS = {"삼겹살": "돼지고기", "목살": "돼지고기", "닭가슴살": "닭고기"}


def compact(value: str) -> str:
    return re.sub(r"[^가-힣a-z]", "", value.lower())


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
    candidates = [(compact(alias), key) for key, aliases in ALIASES.items() for alias in aliases]
    for alias, key in sorted(candidates, key=lambda pair: len(pair[0]), reverse=True):
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
    parts = re.split(r",(?![^()]*\))|\n|[;；]", raw)
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


def rank(recipes: list[dict], foods: list, limit: int = 30) -> list[dict]:
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
        summary.update(matched=[p["text"] for p in matched], missing=[p["text"] for p in missing],
                       missing_main=main_missing)
        scored.append((score, summary))
    # Shuffle before stable sorting and limiting: equal-rank recipes, including
    # those beyond the first 30, have an equal chance on every request.
    random.shuffle(scored)
    scored.sort(key=lambda row: row[0])
    return [row[1] for row in scored[:limit]]
