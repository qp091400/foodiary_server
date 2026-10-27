"""추천에 선택한 식품의 소유권과 현재 상태를 서버에서 검증한다."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import FridgeAccess, get_fridge_access
from app.models import FoodItem
from app.services.recipes import catalogue, rank
from app.timeutil import today

router = APIRouter(prefix="/fridges/{fridge_id}/recipes", tags=["레시피"])


class Selection(BaseModel):
    item_ids: list[int] = Field(max_length=1000)


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
    data = catalogue()
    return {"recipes": rank(data["recipes"], usable), "catalogue_count": len(data["recipes"]),
            "source_total": data["source_total"], "sample": data["sample"],
            "imported_at": data["imported_at"], "excluded_expired": [f.name for f in expired]}


@router.get("/{recipe_id}")
def detail(recipe_id: str, access: FridgeAccess = Depends(get_fridge_access)):
    recipe = next((r for r in catalogue()["recipes"] if r["id"] == recipe_id), None)
    if recipe is None:
        raise HTTPException(404, "레시피를 찾을 수 없습니다. 추천 목록을 다시 불러와 주세요.")
    return recipe
