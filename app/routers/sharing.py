"""냉장고 공유 링크: 링크로 들어오면 주인과 친구가 되고 냉장고를 볼 수 있다(또는 주인처럼 고칠 수 있다).
그리고 주인이 링크와 친구별 권한을 관리한다."""

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import FridgeAccess, get_current_user, require_owner
from app.models import Fridge, FridgeMember, FridgeShareLink, FridgeViewer, User
from app.schemas import AccessOut, AccessUpdate, JoinResult, PublicUser, ShareLinkCreate, ShareLinkOut, ShareLinkPreview
from app.services.friends import explicit_permission, make_friends
from app.timeutil import utcnow

router = APIRouter(tags=["공유 링크"])

MAX_ACTIVE_LINKS = 20  # TODO: balance. 냉장고 하나에 살아 있는 링크 수
LINK_NOT_FOUND = "링크가 올바르지 않거나 만료되었어요."


def _out(link: FridgeShareLink) -> ShareLinkOut:
    base = settings.share_link_base
    return ShareLinkOut(
        id=link.id,
        token=link.token,
        url=base + link.token if base else None,
        permission=link.permission,
        created_at=link.created_at,
        expires_at=link.expires_at,
        max_uses=link.max_uses,
        use_count=link.use_count,
    )


def _usable(link: FridgeShareLink) -> bool:
    return link.revoked_at is None and link.expires_at >= utcnow() and (link.max_uses is None or link.use_count < link.max_uses)


# ── 주인: 링크 만들기와 관리 ─────────────────────────────────────────────


@router.post("/fridges/{fridge_id}/share-links", response_model=ShareLinkOut, status_code=201, summary="공유 링크 만들기 (주인만)")
def create_link(payload: ShareLinkCreate, access: FridgeAccess = Depends(require_owner), db: Session = Depends(get_db)):
    alive = db.scalars(select(FridgeShareLink).where(FridgeShareLink.fridge_id == access.fridge.id, FridgeShareLink.revoked_at.is_(None))).all()
    if sum(_usable(link) for link in alive) >= MAX_ACTIVE_LINKS:
        raise HTTPException(status_code=429, detail=f"살아 있는 링크가 너무 많아요. 안 쓰는 링크를 지운 뒤에 만들어 주세요. (최대 {MAX_ACTIVE_LINKS}개)")
    link = FridgeShareLink(
        fridge_id=access.fridge.id,
        token=secrets.token_urlsafe(16),
        permission=payload.permission,
        expires_at=utcnow() + timedelta(days=payload.expires_in_days),
        max_uses=payload.max_uses,
    )
    db.add(link)
    db.commit()
    return _out(link)


@router.get("/fridges/{fridge_id}/share-links", response_model=list[ShareLinkOut], summary="살아 있는 공유 링크 (주인만)")
def list_links(access: FridgeAccess = Depends(require_owner), db: Session = Depends(get_db)):
    links = db.scalars(
        select(FridgeShareLink).where(FridgeShareLink.fridge_id == access.fridge.id, FridgeShareLink.revoked_at.is_(None)).order_by(FridgeShareLink.id)
    ).all()
    return [_out(link) for link in links if _usable(link)]


@router.delete("/fridges/{fridge_id}/share-links/{link_id}", status_code=204, summary="공유 링크 없애기 (이미 들어온 사람의 권한은 그대로)")
def revoke_link(link_id: int, access: FridgeAccess = Depends(require_owner), db: Session = Depends(get_db)):
    link = db.get(FridgeShareLink, link_id)
    if link is None or link.fridge_id != access.fridge.id or link.revoked_at is not None:
        raise HTTPException(status_code=404, detail="링크를 찾을 수 없어요.")
    link.revoked_at = utcnow()
    db.commit()
    return Response(status_code=204)


# ── 링크로 들어오는 사람 ─────────────────────────────────────────────────


RANK = {None: 0, "viewer": 1, "editor": 2, "owner": 3}


def _find(db: Session, token: str, me: User) -> tuple[FridgeShareLink, Fridge, User, str | None]:
    """링크와 냉장고, 주인, 내가 이미 직접 받은 권한. 만료되었거나 다 쓴 링크도 이미 그만큼의 권한을 가진 사람에게는 열린다
    (한 번 들어온 사람이 링크를 다시 눌렀을 때 "없는 링크"로 보이면 안 된다)."""
    link = db.scalar(select(FridgeShareLink).where(FridgeShareLink.token == token))
    fridge = db.get(Fridge, link.fridge_id) if link else None
    owner = db.get(User, fridge.owner_id) if fridge else None
    if owner is None:
        raise HTTPException(status_code=404, detail=LINK_NOT_FOUND)
    current = explicit_permission(db, me.id, fridge.id)
    if not _usable(link) and RANK[current] < RANK[link.permission]:
        raise HTTPException(status_code=404, detail=LINK_NOT_FOUND)
    return link, fridge, owner, current


@router.get("/share-links/{token}", response_model=ShareLinkPreview, summary="링크를 눌렀을 때 누구의 어떤 냉장고인지 미리 보기")
def preview_link(token: str, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    link, fridge, owner, current = _find(db, token, me)
    return ShareLinkPreview(
        owner=PublicUser.of(owner),
        fridge_name=fridge.name,
        permission=link.permission,
        my_permission="editor" if current in ("owner", "editor") else current or "none",
    )


@router.post("/share-links/{token}/join", response_model=JoinResult, summary="링크로 들어오기: 주인과 친구가 되고 냉장고 권한을 받는다")
def join_link(token: str, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    link, fridge, owner, current = _find(db, token, me)
    if owner.id == me.id:
        raise HTTPException(status_code=400, detail="내 냉장고의 링크예요.")
    newly_friends = make_friends(db, owner.id, me.id)
    granted = current
    if link.permission == "editor":
        if current != "editor":
            if current == "viewer":  # 보기에서 고치기로 올라간다
                db.delete(db.get(FridgeViewer, (fridge.id, me.id)))
            db.add(FridgeMember(fridge_id=fridge.id, user_id=me.id, role="member"))
            granted = "editor"
            link.use_count += 1
    elif current is None:
        db.add(FridgeViewer(fridge_id=fridge.id, user_id=me.id))
        granted = "viewer"
        link.use_count += 1
    # 이미 같거나 더 높은 권한이 있으면 낮추지 않고, 링크 사용 횟수도 세지 않는다
    db.commit()
    return JoinResult(owner=PublicUser.of(owner), fridge_id=fridge.id, fridge_name=fridge.name, permission=granted, newly_friends=newly_friends)


# ── 주인: 누가 어떤 권한을 가졌는지 ──────────────────────────────────────


@router.get("/fridges/{fridge_id}/access", response_model=list[AccessOut], summary="이 냉장고에 권한이 있는 사람들 (주인만)")
def list_access(access: FridgeAccess = Depends(require_owner), db: Session = Depends(get_db)):
    fridge_id = access.fridge.id
    editors = db.execute(
        select(User, FridgeMember.joined_at).join(FridgeMember, FridgeMember.user_id == User.id).where(FridgeMember.fridge_id == fridge_id, FridgeMember.role == "member")
    ).all()
    viewers = db.execute(
        select(User, FridgeViewer.created_at).join(FridgeViewer, FridgeViewer.user_id == User.id).where(FridgeViewer.fridge_id == fridge_id)
    ).all()
    entries = [AccessOut(user=PublicUser.of(user), permission="editor", since=since) for user, since in editors] + [
        AccessOut(user=PublicUser.of(user), permission="viewer", since=since) for user, since in viewers
    ]
    return sorted(entries, key=lambda entry: (entry.since, entry.user.id))


def _holder(db: Session, fridge_id: int, user_id: int) -> str:
    current = explicit_permission(db, user_id, fridge_id)
    if current not in ("editor", "viewer"):  # 주인이거나 권한이 없다
        raise HTTPException(status_code=404, detail="이 냉장고에 권한이 있는 사람이 아니에요.")
    return current


@router.patch("/fridges/{fridge_id}/access/{user_id}", response_model=AccessOut, summary="권한 바꾸기: viewer(보기+변경 요청) / editor(주인처럼 고치기) (주인만)")
def change_access(user_id: int, payload: AccessUpdate, access: FridgeAccess = Depends(require_owner), db: Session = Depends(get_db)):
    fridge_id = access.fridge.id
    current = _holder(db, fridge_id, user_id)
    if current != payload.permission:
        if payload.permission == "editor":
            db.delete(db.get(FridgeViewer, (fridge_id, user_id)))
            db.add(FridgeMember(fridge_id=fridge_id, user_id=user_id, role="member"))
        else:
            db.delete(db.scalar(select(FridgeMember).where(FridgeMember.fridge_id == fridge_id, FridgeMember.user_id == user_id)))
            db.add(FridgeViewer(fridge_id=fridge_id, user_id=user_id))
        db.commit()
    return AccessOut(
        user=PublicUser.of(db.get(User, user_id)), permission=payload.permission, since=_since(db, fridge_id, user_id, payload.permission)
    )


def _since(db: Session, fridge_id: int, user_id: int, permission: str):
    if permission == "editor":
        return db.scalar(select(FridgeMember.joined_at).where(FridgeMember.fridge_id == fridge_id, FridgeMember.user_id == user_id))
    return db.scalar(select(FridgeViewer.created_at).where(FridgeViewer.fridge_id == fridge_id, FridgeViewer.user_id == user_id))


@router.delete("/fridges/{fridge_id}/access/{user_id}", status_code=204, summary="이 냉장고의 권한 거두기 (친구는 그대로) (주인만)")
def remove_access(user_id: int, access: FridgeAccess = Depends(require_owner), db: Session = Depends(get_db)):
    fridge_id = access.fridge.id
    if _holder(db, fridge_id, user_id) == "editor":
        db.delete(db.scalar(select(FridgeMember).where(FridgeMember.fridge_id == fridge_id, FridgeMember.user_id == user_id)))
    else:
        db.delete(db.get(FridgeViewer, (fridge_id, user_id)))
    db.commit()
    return Response(status_code=204)
