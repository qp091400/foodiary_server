"""냉장고 묶음(예: 집, 부모님 댁). 사람마다 자기 묶음을 만들고, 냉장고 하나는 여러 묶음에 들어갈 수 있다.
묶음은 통계 고르기와 냉장고 목록 정리에 쓰고, 친구에게 공유하면 묶음 안에서 내가 주인인 냉장고의 권한이 한꺼번에 따라간다."""

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import FRIDGE_NOT_FOUND, get_current_user
from app.models import Fridge, FridgeGroup, FridgeGroupItem, FridgeGroupLink, FridgeGroupShare, User
from app.schemas import (
    FridgeGroupCreate,
    FridgeGroupOut,
    FridgeGroupUpdate,
    GroupFridgeOut,
    GroupJoinResult,
    GroupLinkPreview,
    GroupShareOut,
    GroupShareUpdate,
    PublicUser,
    ShareLinkCreate,
    ShareLinkOut,
)
from app.services.access import RANK, recompute_for_group, recompute_group_grants
from app.services.friends import are_friends, fridge_permission, make_friends
from app.services.groups import group_fridges, visible_group
from app.timeutil import utcnow

router = APIRouter(tags=["냉장고 묶음"])

MAX_GROUPS_PER_USER = 10  # TODO: balance. 내가 만든 묶음 수
MAX_FRIDGES_PER_GROUP = 50  # TODO: balance
MAX_ACTIVE_GROUP_LINKS = 20  # TODO: balance. 묶음 하나에 살아 있는 링크 수
GROUP_NOT_FOUND = "냉장고 묶음을 찾을 수 없어요."
LINK_NOT_FOUND = "링크가 올바르지 않거나 만료되었어요."
NOT_FRIEND = "친구가 아니에요. 친구가 아닌 사람에게는 묶음 링크로 공유해요."


def _my_group(db: Session, me: User, group_id: int) -> FridgeGroup:
    """고치거나 공유할 수 있는 내 묶음. 남에게 공유받은 묶음은 보기만 되므로 403."""
    group = visible_group(db, me.id, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail=GROUP_NOT_FOUND)
    if group.owner_id != me.id:
        raise HTTPException(status_code=403, detail="묶음 주인만 할 수 있어요.")
    return group


def _out(db: Session, me: User, group: FridgeGroup) -> FridgeGroupOut:
    fridges = []
    for fridge in group_fridges(db, group):
        permission = fridge_permission(db, me.id, fridge)
        if permission is not None:  # 내가 볼 수 없는 냉장고는 묶음에 있어도 보이지 않는다
            fridges.append(GroupFridgeOut(id=fridge.id, name=fridge.name, owner=PublicUser.of(db.get(User, fridge.owner_id)), my_permission=permission))
    mine = group.owner_id == me.id
    shares = (
        [
            GroupShareOut(user=PublicUser.of(db.get(User, share.user_id)), permission=share.permission, since=share.created_at)
            for share in db.scalars(select(FridgeGroupShare).where(FridgeGroupShare.group_id == group.id).order_by(FridgeGroupShare.id)).all()
        ]
        if mine
        else []
    )
    my_share = None if mine else db.scalar(select(FridgeGroupShare.permission).where(FridgeGroupShare.group_id == group.id, FridgeGroupShare.user_id == me.id))
    return FridgeGroupOut(
        id=group.id,
        name=group.name,
        owner=PublicUser.of(db.get(User, group.owner_id)),
        mine=mine,
        my_share=my_share,
        fridges=fridges,
        shares=shares,
        created_at=group.created_at,
    )


# ── 묶음 만들기와 관리 ───────────────────────────────────────────────────


@router.get("/fridge-groups", response_model=list[FridgeGroupOut], summary="내 묶음과 친구가 나에게 공유한 묶음 (공유받은 묶음은 보기만 된다)")
def list_groups(me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    own = db.scalars(select(FridgeGroup).where(FridgeGroup.owner_id == me.id).order_by(FridgeGroup.id)).all()
    shared = db.scalars(
        select(FridgeGroup)
        .join(FridgeGroupShare, FridgeGroupShare.group_id == FridgeGroup.id)
        .where(FridgeGroupShare.user_id == me.id)
        .order_by(FridgeGroup.id)
    ).all()
    return [_out(db, me, group) for group in (*own, *shared)]


@router.post("/fridge-groups", response_model=FridgeGroupOut, status_code=201, summary="새 묶음 만들기")
def create_group(payload: FridgeGroupCreate, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if db.scalar(select(func.count()).select_from(FridgeGroup).where(FridgeGroup.owner_id == me.id)) >= MAX_GROUPS_PER_USER:
        raise HTTPException(status_code=409, detail=f"묶음은 {MAX_GROUPS_PER_USER}개까지 만들 수 있어요. 안 쓰는 묶음을 지운 뒤에 만들어 주세요.")
    group = FridgeGroup(owner_id=me.id, name=payload.name)
    db.add(group)
    db.commit()
    return _out(db, me, group)


@router.get("/fridge-groups/{group_id}", response_model=FridgeGroupOut, summary="묶음 하나 (내 묶음이거나 나에게 공유된 묶음)")
def get_group(group_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = visible_group(db, me.id, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail=GROUP_NOT_FOUND)
    return _out(db, me, group)


@router.patch("/fridge-groups/{group_id}", response_model=FridgeGroupOut, summary="묶음 이름 바꾸기 (묶음 주인만)")
def rename_group(group_id: int, payload: FridgeGroupUpdate, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = _my_group(db, me, group_id)
    group.name = payload.name
    db.commit()
    return _out(db, me, group)


@router.delete("/fridge-groups/{group_id}", status_code=204, summary="묶음 지우기 (묶음에서 따라간 친구들의 권한도 거둔다. 냉장고는 그대로) (묶음 주인만)")
def delete_group(group_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = _my_group(db, me, group_id)
    shared_with = db.scalars(select(FridgeGroupShare.user_id).where(FridgeGroupShare.group_id == group.id)).all()
    for model in (FridgeGroupLink, FridgeGroupShare, FridgeGroupItem):
        db.execute(delete(model).where(model.group_id == group.id))
    db.delete(group)
    db.flush()
    for user_id in shared_with:
        recompute_group_grants(db, me.id, user_id)
    db.commit()
    return Response(status_code=204)


@router.put("/fridge-groups/{group_id}/fridges/{fridge_id}", response_model=FridgeGroupOut, summary="묶음에 냉장고 넣기 (내가 볼 수 있는 냉장고면 된다. 이미 들어 있어도 괜찮다)")
def add_fridge(group_id: int, fridge_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = _my_group(db, me, group_id)
    fridge = db.get(Fridge, fridge_id)
    if fridge is None or fridge_permission(db, me.id, fridge) is None:
        raise HTTPException(status_code=404, detail=FRIDGE_NOT_FOUND)
    if db.get(FridgeGroupItem, (group.id, fridge.id)) is None:
        count = db.scalar(select(func.count()).select_from(FridgeGroupItem).where(FridgeGroupItem.group_id == group.id))
        if count >= MAX_FRIDGES_PER_GROUP:
            raise HTTPException(status_code=409, detail=f"한 묶음에는 냉장고를 {MAX_FRIDGES_PER_GROUP}개까지 넣을 수 있어요.")
        db.add(FridgeGroupItem(group_id=group.id, fridge_id=fridge.id))
        db.flush()
        recompute_for_group(db, group)
    db.commit()
    return _out(db, me, group)


@router.delete("/fridge-groups/{group_id}/fridges/{fridge_id}", status_code=204, summary="묶음에서 냉장고 빼기 (냉장고는 그대로. 이 묶음 때문에 받았던 친구들의 권한은 거둔다)")
def remove_fridge(group_id: int, fridge_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = _my_group(db, me, group_id)
    item = db.get(FridgeGroupItem, (group.id, fridge_id))
    if item is None:
        raise HTTPException(status_code=404, detail="이 묶음에 없는 냉장고예요.")
    db.delete(item)
    db.flush()
    recompute_for_group(db, group)
    db.commit()
    return Response(status_code=204)


# ── 친구에게 묶음 공유 ───────────────────────────────────────────────────


def _share_out(db: Session, share: FridgeGroupShare) -> GroupShareOut:
    return GroupShareOut(user=PublicUser.of(db.get(User, share.user_id)), permission=share.permission, since=share.created_at)


@router.put(
    "/fridge-groups/{group_id}/shares/{user_id}",
    response_model=GroupShareOut,
    summary="친구에게 묶음 공유하기 / 권한 바꾸기: 묶음 안에서 내가 주인인 냉장고마다 viewer(보기) 또는 editor(고치기)가 따라간다",
)
def share_group(group_id: int, user_id: int, payload: GroupShareUpdate, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = _my_group(db, me, group_id)
    if user_id == me.id or db.get(User, user_id) is None or not are_friends(db, me.id, user_id):
        raise HTTPException(status_code=404, detail=NOT_FRIEND)
    share = db.scalar(select(FridgeGroupShare).where(FridgeGroupShare.group_id == group.id, FridgeGroupShare.user_id == user_id))
    if share is None:
        share = FridgeGroupShare(group_id=group.id, user_id=user_id, permission=payload.permission)
        db.add(share)
    else:
        share.permission = payload.permission
    db.flush()
    recompute_group_grants(db, me.id, user_id)
    db.commit()
    return _share_out(db, share)


@router.delete("/fridge-groups/{group_id}/shares/{user_id}", status_code=204, summary="친구와 묶음 공유 끊기 (묶음 때문에 받았던 권한만 거둔다. 따로 준 권한과 친구는 그대로)")
def unshare_group(group_id: int, user_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = _my_group(db, me, group_id)
    share = db.scalar(select(FridgeGroupShare).where(FridgeGroupShare.group_id == group.id, FridgeGroupShare.user_id == user_id))
    if share is None:
        raise HTTPException(status_code=404, detail="이 묶음을 공유받은 사람이 아니에요.")
    db.delete(share)
    db.flush()
    recompute_group_grants(db, me.id, user_id)
    db.commit()
    return Response(status_code=204)


# ── 묶음 링크 ────────────────────────────────────────────────────────────


def _link_out(link: FridgeGroupLink) -> ShareLinkOut:
    base = settings.share_link_base
    return ShareLinkOut(
        id=link.id,
        token=link.token,
        url=base + "group/" + link.token if base else None,  # 냉장고 링크(base + token)와 겹치지 않게 group/ 을 붙인다
        permission=link.permission,
        created_at=link.created_at,
        expires_at=link.expires_at,
        max_uses=link.max_uses,
        use_count=link.use_count,
    )


def _usable(link: FridgeGroupLink) -> bool:
    return link.revoked_at is None and link.expires_at >= utcnow() and (link.max_uses is None or link.use_count < link.max_uses)


@router.post("/fridge-groups/{group_id}/links", response_model=ShareLinkOut, status_code=201, summary="묶음 공유 링크 만들기 (묶음 주인만)")
def create_group_link(group_id: int, payload: ShareLinkCreate, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = _my_group(db, me, group_id)
    alive = db.scalars(select(FridgeGroupLink).where(FridgeGroupLink.group_id == group.id, FridgeGroupLink.revoked_at.is_(None))).all()
    if sum(_usable(link) for link in alive) >= MAX_ACTIVE_GROUP_LINKS:
        raise HTTPException(status_code=429, detail=f"살아 있는 링크가 너무 많아요. 안 쓰는 링크를 지운 뒤에 만들어 주세요. (최대 {MAX_ACTIVE_GROUP_LINKS}개)")
    link = FridgeGroupLink(
        group_id=group.id,
        token=secrets.token_urlsafe(16),
        permission=payload.permission,
        expires_at=utcnow() + timedelta(days=payload.expires_in_days),
        max_uses=payload.max_uses,
    )
    db.add(link)
    db.commit()
    return _link_out(link)


@router.get("/fridge-groups/{group_id}/links", response_model=list[ShareLinkOut], summary="살아 있는 묶음 링크 (묶음 주인만)")
def list_group_links(group_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = _my_group(db, me, group_id)
    links = db.scalars(
        select(FridgeGroupLink).where(FridgeGroupLink.group_id == group.id, FridgeGroupLink.revoked_at.is_(None)).order_by(FridgeGroupLink.id)
    ).all()
    return [_link_out(link) for link in links if _usable(link)]


@router.delete("/fridge-groups/{group_id}/links/{link_id}", status_code=204, summary="묶음 링크 없애기 (이미 들어온 사람의 권한은 그대로) (묶음 주인만)")
def revoke_group_link(group_id: int, link_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = _my_group(db, me, group_id)
    link = db.get(FridgeGroupLink, link_id)
    if link is None or link.group_id != group.id or link.revoked_at is not None:
        raise HTTPException(status_code=404, detail="링크를 찾을 수 없어요.")
    link.revoked_at = utcnow()
    db.commit()
    return Response(status_code=204)


def _find_link(db: Session, token: str, me: User) -> tuple[FridgeGroupLink, FridgeGroup, User, str | None]:
    """링크와 묶음, 주인, 내가 이미 받은 묶음 권한. 만료되었거나 다 쓴 링크도 이미 그만큼 받은 사람에게는 열린다."""
    link = db.scalar(select(FridgeGroupLink).where(FridgeGroupLink.token == token))
    group = db.get(FridgeGroup, link.group_id) if link else None
    owner = db.get(User, group.owner_id) if group else None
    if owner is None:
        raise HTTPException(status_code=404, detail=LINK_NOT_FOUND)
    current = db.scalar(select(FridgeGroupShare.permission).where(FridgeGroupShare.group_id == group.id, FridgeGroupShare.user_id == me.id))
    if not _usable(link) and RANK[current] < RANK[link.permission]:
        raise HTTPException(status_code=404, detail=LINK_NOT_FOUND)
    return link, group, owner, current


@router.get("/group-links/{token}", response_model=GroupLinkPreview, summary="묶음 링크를 눌렀을 때 누구의 어떤 묶음인지 미리 보기")
def preview_group_link(token: str, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    link, group, owner, current = _find_link(db, token, me)
    fridge_count = sum(fridge.owner_id == owner.id for fridge in group_fridges(db, group))
    return GroupLinkPreview(owner=PublicUser.of(owner), group_name=group.name, permission=link.permission, my_share=current or "none", fridge_count=fridge_count)


@router.post("/group-links/{token}/join", response_model=GroupJoinResult, summary="묶음 링크로 들어오기: 주인과 친구가 되고 묶음 안 냉장고의 권한을 받는다")
def join_group_link(token: str, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    link, group, owner, current = _find_link(db, token, me)
    if owner.id == me.id:
        raise HTTPException(status_code=400, detail="내 묶음의 링크예요.")
    newly_friends = make_friends(db, owner.id, me.id)
    granted = current
    if RANK[link.permission] > RANK[current]:  # 권한은 링크로는 올라가기만 한다
        if current is None:
            db.add(FridgeGroupShare(group_id=group.id, user_id=me.id, permission=link.permission))
        else:
            db.scalar(select(FridgeGroupShare).where(FridgeGroupShare.group_id == group.id, FridgeGroupShare.user_id == me.id)).permission = link.permission
        granted = link.permission
        link.use_count += 1
    db.flush()
    recompute_group_grants(db, owner.id, me.id)
    db.commit()
    return GroupJoinResult(owner=PublicUser.of(owner), group_id=group.id, group_name=group.name, permission=granted, newly_friends=newly_friends)
