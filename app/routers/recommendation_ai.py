"""Authenticated model delivery and private behavioral signals."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import get_db
from app.deps import get_current_user
from app.models import RecipeEvent, RecipeReview, User
from app.services import recipe_store, recommendation_bundle

router = APIRouter(prefix='/recommendation-ai',tags=['개인화 추천'])


class EventIn(BaseModel):
    recipe_id: str = Field(min_length=1,max_length=100)
    event_key: UUID
    event_type: Literal['view', 'select']
    source: Literal['fridge', 'recipe_detail']


@router.post('/events')
def event(payload: EventIn, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    existing = db.scalar(select(RecipeEvent).where(RecipeEvent.user_id==me.id,RecipeEvent.event_key==str(payload.event_key)))
    if existing:
        if (existing.recipe_id,existing.event_type,existing.source) != (payload.recipe_id,payload.event_type,payload.source):
            raise HTTPException(409,'같은 기록 번호의 내용이 달라요.')
        return {'id':existing.id}
    if recipe_store.detail(payload.recipe_id) is None:
        raise HTTPException(404,'레시피를 찾을 수 없어요.')
    row = RecipeEvent(user_id=me.id,recipe_id=payload.recipe_id,event_key=str(payload.event_key),event_type=payload.event_type,source=payload.source)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(RecipeEvent).where(RecipeEvent.user_id==me.id,RecipeEvent.event_key==str(payload.event_key)))
        if existing and (existing.recipe_id,existing.event_type,existing.source)==(payload.recipe_id,payload.event_type,payload.source):
            return {'id':existing.id}
        raise HTTPException(409,'기록이 변경됐어요. 다시 시도해주세요.') from None
    return {'id':row.id}


@router.get('/model')
def get_model(me: User = Depends(get_current_user)):
    try:
        value = recommendation_bundle.model()
    except (OSError,ValueError,KeyError):
        raise HTTPException(503,'추천 모델을 준비하고 있어요.') from None
    if value is None:
        raise HTTPException(503,'추천 모델을 준비하고 있어요.')
    return value


def personal_signals(db, user_id):
    signals = {rid: {'recipeId':rid,'selections':count} for rid,count in db.execute(
        select(RecipeEvent.recipe_id,func.count()).where(RecipeEvent.user_id==user_id,RecipeEvent.event_type=='select').group_by(RecipeEvent.recipe_id))}
    for rid,rating in db.execute(select(RecipeReview.recipe_id,RecipeReview.rating).where(RecipeReview.author_id==user_id,RecipeReview.deleted_at.is_(None))):
        signals.setdefault(rid,{'recipeId':rid,'selections':0})['rating']=rating
    return list(signals.values())
