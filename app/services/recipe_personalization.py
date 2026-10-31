"""Explainable content-based preferences, derived only from this user's live reviews."""
import hashlib
import json
import re
from collections import defaultdict
from fractions import Fraction

from sqlalchemy import select
from app.models import RecipeReview

TAGS = {
    "매콤": ("매콤", "매운", "칼칼", "고추장", "고춧가루"),
    "담백": ("담백", "순한", "맑은"), "바삭": ("바삭", "튀김", "전분"),
    "고소": ("고소", "들깨", "참깨", "치즈"), "국물": ("국물", "수프", "찌개", "탕"),
    "간편": ("간단", "간편", "전자레인지", "원팬", "5분", "10분"),
}

def features(recipe):
    result = {"재료:" + part["key"] for part in recipe.get("ingredients", []) if part.get("key") and not part.get("staple")}
    for key, label in (("method", "조리법"), ("category", "분류")):
        if recipe.get(key): result.add(label + ":" + recipe[key])
    title = " ".join(str(recipe.get(key) or "") for key in ("name", "display_name", "method", "cooking_time"))
    for tag, aliases in TAGS.items():
        if any(alias in title for alias in aliases): result.add("특징:" + tag)
    return result

class PreferenceProfile:
    def __init__(self, reviews=(), household_size=None, detail=None):
        self.weights = defaultdict(float)
        self.evidence = defaultdict(list)
        self.count = len(reviews)
        raw = [(r.id, r.recipe_id, r.rating, r.body, str(r.updated_at)) for r in reviews]
        self.version = hashlib.sha256(json.dumps([household_size, raw], ensure_ascii=False).encode()).hexdigest()[:20]
        for review in reviews:
            weight = {1: -2, 2: -1, 3: 0, 4: 1, 5: 2}[review.rating]
            recipe = detail(review.recipe_id) if detail else None
            values = features(recipe) if recipe else set()
            if not recipe: continue
            body = review.body or ""
            # Only explicit, attributable taste statements; ambiguous text is not treated as sentiment.
            for tag in TAGS:
                negative = re.search(rf"{tag}(?:한|해서|해서는|하고|은|한\s*맛이|한\s*음식이|한\s*맛은|한\s*음식은)?\s*(?:싫|부담|별로|못\s*먹)", body)
                positive = re.search(rf"{tag}(?:한|해서|하고|한\s*맛이|한\s*음식이)?\s*(?:좋|마음에|만족)", body)
                if negative or positive:
                    feature = "특징:" + tag
                    amount = -2 if negative else 2
                    self.weights[feature] += amount
                    self.evidence[feature].append((review, amount))
            for feature in values:
                self.weights[feature] += weight
                self.evidence[feature].append((review, weight))
        # Bound the influence of repeated similar reviews so feedback cannot erase ingredient fit.
        self.weights = {key: max(-4, min(4, value)) for key, value in self.weights.items()}

    def score(self, recipe):
        values = features(recipe)
        score = sum(self.weights.get(key, 0) for key in values) / max(1, len(values) ** 0.5)
        return score

    def explain(self, recipe):
        positive = sorted((key for key in features(recipe) if self.weights.get(key, 0) > 0), key=lambda key: -self.weights[key])
        evidence, used = [], set()
        for key in positive:
            for review, amount in sorted(self.evidence[key], key=lambda row: -row[1]):
                if amount <= 0 or review.id in used: continue
                used.add(review.id)
                evidence.append({"review_id": review.id, "recipe_id": review.recipe_id, "recipe_name": review.recipe_name,
                                 "rating": review.rating, "feature": key})
                break
            if len(evidence) >= 2: break
        reasons = [f"내가 별점 {item['rating']}점을 남긴 ‘{item['recipe_name']}’와 {item['feature'].split(':', 1)[1]} 특성이 비슷해요" for item in evidence]
        if not reasons:
            reasons = ["선택한 재료와 추가로 필요한 재료 수를 기준으로 골랐어요"]
        return {"recommendation_reasons": reasons, "preference_evidence": evidence}

def load_profile(db, user):
    from app.services import recipe_store
    reviews = db.scalars(select(RecipeReview).where(RecipeReview.author_id == user.id,
        RecipeReview.deleted_at.is_(None)).order_by(RecipeReview.id.desc()).limit(100)).all()
    reviews = [r for r in reviews if not (r.body or "").startswith(("[시연용", "[검색 시연용"))]
    return PreferenceProfile(reviews, user.household_size, recipe_store.detail)

def serving_count(value):
    match = re.fullmatch(r"\s*(\d+)\s*인분(?:\s*기준)?\s*", str(value or ""))
    return int(match[1]) if match and 1 <= int(match[1]) <= 100 else None

def serving_distance(value, target):
    source = serving_count(value)
    return abs(source - target) if source and target else 100

def portions(recipe, target):
    result = dict(recipe)
    original = recipe.get("servings") or ""
    source = serving_count(original)
    result.update(original_servings=original, target_servings=target, source_servings=source)
    if not target:
        result["portion_note"] = "내 정보에서 가구 인원을 설정하면 인원에 맞춰 추천해요."
        return result
    if not source:
        result["portion_note"] = f"{target}명 식사 기준 · 원본 인분 정보가 없어 재료 양은 원문대로 표시해요."
        return result
    factor = Fraction(target, source)
    result.update(servings=f"{target}인분", serving_scale=float(factor))
    adjusted = []
    for part in recipe.get("ingredients", []):
        item = dict(part)
        item["original_text"] = part["text"]
        amount = part["text"][len(part["name"]):].strip() if part["text"].startswith(part["name"]) else ""
        match = re.fullmatch(r"(\d+(?:\.\d+)?|\d+/\d+)\s*(kg|g|ml|mL|L|l|개|장|컵|큰술|작은술|마리|쪽|캔|봉|T|t)", amount)
        if match and not re.fullmatch(r"\d+/0+", match[1]):
            number = float(Fraction(match[1]) * factor)
            item["text"] = f"{part['name']} {number:.2f}".rstrip("0").rstrip(".") + match[2]
            item["amount_adjusted"] = True
        else:
            item["amount_adjusted"] = False
        adjusted.append(item)
    if "ingredients" in recipe:
        result["ingredients"] = adjusted
        result["ingredients_text"] = ", ".join(p["text"] for p in adjusted)
    result["portion_note"] = f"원본 {source}인분 → {target}인분 기준. 수치가 명확한 재료만 {float(factor):g}배로 계산했어요. 약간·취향껏·복합 표기는 원문을 확인하고, 조리 시간과 온도는 양에 맞게 확인해주세요."
    return result
