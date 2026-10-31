"""추천에 선택한 식품의 소유권과 현재 상태를 서버에서 검증한다. 추천 카드와 상세에는 레시피 별점도 함께 보낸다."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import FridgeAccess, get_fridge_access
from app.models import FoodItem, RecipeReview
from app.services import recipe_store
from app.services.recipe_reviews import rating_summaries
from app.services.recipe_personalization import load_profile
from app.timeutil import today

router = APIRouter(prefix="/fridges/{fridge_id}/recipes", tags=["레시피"])


class Selection(BaseModel):
    item_ids: list[int] = Field(max_length=1000)
    offset: int = Field(0, ge=0, le=1000000)
    seed: int | None = Field(None, ge=0, le=2147483647)
    q: str = Field('', max_length=100)
    profile_version: str | None = Field(None, max_length=20)


@router.post("/recommendations")
def recommendations(payload: Selection, access: FridgeAccess = Depends(get_fridge_access),
                    db: Session = Depends(get_db)):
    ids = set(payload.item_ids)
    foods = list(db.scalars(select(FoodItem).where(FoodItem.fridge_id == access.fridge.id,
                         FoodItem.id.in_(ids), FoodItem.status == "active", FoodItem.remaining_quantity > 0)))
    if len(foods) != len(ids):
        raise HTTPException(409, "선택한 식품이 변경됐어요. 냉장고 목록을 새로 불러와 주세요.")
    expired = [f for f in foods if f.expiry_date and f.expiry_date < today()]
    usable = [f for f in foods if f not in expired]
    profile = load_profile(db, access.user)
    if payload.profile_version and payload.profile_version != profile.version:
        raise HTTPException(409, "후기 정보가 바뀌었어요. 추천 새로고침을 눌러주세요.")
    result = recipe_store.recommendations(usable, offset=payload.offset, seed=payload.seed, query=payload.q.strip(), profile=profile)
    summaries = rating_summaries(db, [recipe["id"] for recipe in result["recipes"]])
    for recipe in result["recipes"]:
        rating = summaries[recipe["id"]]
        recipe["rating_average"], recipe["rating_count"] = rating.average, rating.count
        recipe.update(profile.explain(recipe_store.detail(recipe['id']) or recipe))
    return {**result, "excluded_expired": [f.name for f in expired],
            "profile_version": profile.version, "preference_review_count": profile.count, "query": payload.q.strip()}


@router.get("/{recipe_id}")
def detail(recipe_id: str, access: FridgeAccess = Depends(get_fridge_access), db: Session = Depends(get_db)):
    recipe = recipe_store.detail(recipe_id)
    if recipe is None:
        raise HTTPException(404, "레시피를 찾을 수 없습니다. 추천 목록을 다시 불러와 주세요.")
    mine = db.scalar(select(RecipeReview.id).where(RecipeReview.recipe_id == recipe_id, RecipeReview.author_id == access.user.id,
                                                   RecipeReview.deleted_at.is_(None)))
    # 후기 목록·쓰기는 /recipes/{recipe_id}/reviews. 여기서는 별점 요약과 내가 썼는지만 알려 준다.
    return {**recipe, "rating": rating_summaries(db, [recipe_id])[recipe_id], "my_review_id": mine}
