"""Fridge groups (e.g. home, parents' place). Each person makes their own groups; a fridge may be in several.
Groups organise the fridge list and the statistics picker. They grant no access: sharing stays per fridge (sharing.py)."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import FRIDGE_NOT_FOUND, get_current_user
from app.models import Fridge, FridgeGroup, FridgeGroupItem, User
from app.schemas import FridgeGroupCreate, FridgeGroupOut, FridgeGroupUpdate, GroupFridgeOut, PublicUser
from app.services.fridge_access import fridge_permission
from app.services.groups import group_fridges, my_group

router = APIRouter(tags=["냉장고 묶음"])

MAX_GROUPS_PER_USER = 10  # TODO: balance
MAX_FRIDGES_PER_GROUP = 50  # TODO: balance
GROUP_NOT_FOUND = "냉장고 묶음을 찾을 수 없어요."


def _mine(db: Session, me: User, group_id: int) -> FridgeGroup:
    group = my_group(db, me.id, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail=GROUP_NOT_FOUND)
    return group


def _out(db: Session, me: User, group: FridgeGroup) -> FridgeGroupOut:
    fridges = []
    for fridge in group_fridges(db, group):
        permission = fridge_permission(db, me.id, fridge)
        if permission is not None:  # a fridge I lost access to stays in the group but is hidden
            fridges.append(GroupFridgeOut(id=fridge.id, name=fridge.name, owner=PublicUser.of(db.get(User, fridge.owner_id)), my_permission=permission))
    return FridgeGroupOut(id=group.id, name=group.name, fridges=fridges, created_at=group.created_at)


@router.get("/fridge-groups", response_model=list[FridgeGroupOut], summary="내 냉장고 묶음 목록")
def list_groups(me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    groups = db.scalars(select(FridgeGroup).where(FridgeGroup.owner_id == me.id).order_by(FridgeGroup.id)).all()
    return [_out(db, me, group) for group in groups]


@router.post("/fridge-groups", response_model=FridgeGroupOut, status_code=201, summary="새 묶음 만들기")
def create_group(payload: FridgeGroupCreate, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if db.scalar(select(func.count()).select_from(FridgeGroup).where(FridgeGroup.owner_id == me.id)) >= MAX_GROUPS_PER_USER:
        raise HTTPException(status_code=409, detail=f"묶음은 {MAX_GROUPS_PER_USER}개까지 만들 수 있어요. 안 쓰는 묶음을 지운 뒤에 만들어 주세요.")
    group = FridgeGroup(owner_id=me.id, name=payload.name)
    db.add(group)
    db.commit()
    return _out(db, me, group)


@router.get("/fridge-groups/{group_id}", response_model=FridgeGroupOut, summary="묶음 하나")
def get_group(group_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _out(db, me, _mine(db, me, group_id))


@router.patch("/fridge-groups/{group_id}", response_model=FridgeGroupOut, summary="묶음 이름 바꾸기")
def rename_group(group_id: int, payload: FridgeGroupUpdate, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = _mine(db, me, group_id)
    group.name = payload.name
    db.commit()
    return _out(db, me, group)


@router.delete("/fridge-groups/{group_id}", status_code=204, summary="묶음 지우기 (냉장고는 그대로)")
def delete_group(group_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = _mine(db, me, group_id)
    db.execute(delete(FridgeGroupItem).where(FridgeGroupItem.group_id == group.id))
    db.delete(group)
    db.commit()
    return Response(status_code=204)


@router.put("/fridge-groups/{group_id}/fridges/{fridge_id}", response_model=FridgeGroupOut, summary="묶음에 냉장고 넣기 (내가 볼 수 있는 냉장고면 된다. 이미 들어 있어도 괜찮다)")
def add_fridge(group_id: int, fridge_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = _mine(db, me, group_id)
    fridge = db.get(Fridge, fridge_id)
    if fridge is None or fridge_permission(db, me.id, fridge) is None:
        raise HTTPException(status_code=404, detail=FRIDGE_NOT_FOUND)
    if db.get(FridgeGroupItem, (group.id, fridge.id)) is None:
        count = db.scalar(select(func.count()).select_from(FridgeGroupItem).where(FridgeGroupItem.group_id == group.id))
        if count >= MAX_FRIDGES_PER_GROUP:
            raise HTTPException(status_code=409, detail=f"한 묶음에는 냉장고를 {MAX_FRIDGES_PER_GROUP}개까지 넣을 수 있어요.")
        db.add(FridgeGroupItem(group_id=group.id, fridge_id=fridge.id))
        db.commit()
    return _out(db, me, group)


@router.delete("/fridge-groups/{group_id}/fridges/{fridge_id}", status_code=204, summary="묶음에서 냉장고 빼기 (냉장고는 그대로)")
def remove_fridge(group_id: int, fridge_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = _mine(db, me, group_id)
    item = db.get(FridgeGroupItem, (group.id, fridge_id))
    if item is None:
        raise HTTPException(status_code=404, detail="이 묶음에 없는 냉장고예요.")
    db.delete(item)
    db.commit()
    return Response(status_code=204)
