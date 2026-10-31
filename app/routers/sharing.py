"""냉장고 공유 링크와 QR: 냉장고마다 링크가 하나 있고(에브리타임 시간표 공유처럼), 링크로 들어온 사람은 그 냉장고의 멤버가 되어
주인처럼 고칠 수 있다. 들어온 냉장고는 내 냉장고 목록(GET /fridges)에 보인다. 친구 관계는 없다."""

import io
import secrets
from typing import Literal

import segno
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import FridgeAccess, get_current_user, get_fridge_access, require_owner
from app.models import Fridge, FridgeMember, User
from app.routers.fridges import fridge_out
from app.schemas import JoinResult, PublicUser, ShareLinkOut, ShareLinkPreview
from app.services.fridge_access import member_permission

router = APIRouter(tags=["공유 링크"])

LINK_NOT_FOUND = "링크가 올바르지 않거나 바뀌었어요. 냉장고 멤버에게 새 링크를 받아 주세요."
QR_SCALE = 8  # QR 한 칸의 픽셀 수. 휴대폰 화면에 띄워 다른 휴대폰으로 찍기에 충분하다
QR_MEDIA = {"png": "image/png", "svg": "image/svg+xml"}


def _new_token() -> str:
    return secrets.token_urlsafe(16)  # 22자. 아무거나 넣어 맞힐 수 없을 만큼 길다


def _ensure_token(db: Session, fridge: Fridge) -> str:
    """처음 공유할 때 링크를 만든다. 두 사람이 거의 동시에 눌러도 같은 링크(먼저 저장된 것)를 받는다."""
    if fridge.share_token is None:
        db.execute(update(Fridge).where(Fridge.id == fridge.id, Fridge.share_token.is_(None)).values(share_token=_new_token()))
        db.commit()
        db.refresh(fridge)
    return fridge.share_token


def _link(token: str) -> ShareLinkOut:
    base = settings.share_link_base
    return ShareLinkOut(token=token, url=base + token if base else None)


# ── 멤버: 링크와 QR 받기 ────────────────────────────────────────────────


@router.post(
    "/fridges/{fridge_id}/share-link",
    response_model=ShareLinkOut,
    summary="공유 링크 받기 (멤버 누구나). 처음 누르면 만들어지고, 그다음부터는 같은 링크",
)
def share_link(access: FridgeAccess = Depends(get_fridge_access), db: Session = Depends(get_db)):
    return _link(_ensure_token(db, access.fridge))


@router.get(
    "/fridges/{fridge_id}/share-link/qr",
    response_class=Response,
    responses={200: {"content": {"image/png": {}, "image/svg+xml": {}}, "description": "공유 링크 주소를 담은 QR"}},
    summary="공유 링크 QR 이미지 (멤버 누구나). 휴대폰 카메라로 찍으면 링크가 열린다. 링크가 아직 없으면 만든다",
)
def share_link_qr(
    image: Literal["png", "svg"] = Query("png", alias="format", description="png(사진으로 저장하거나 보내기) / svg(화면에 크게 그리기)"),
    access: FridgeAccess = Depends(get_fridge_access),
    db: Session = Depends(get_db),
):
    url = _link(_ensure_token(db, access.fridge)).url
    if url is None:  # 토큰만으로는 카메라가 열 수 있는 주소가 아니다
        raise HTTPException(status_code=503, detail="서버에 공유 링크 주소(SHARE_LINK_BASE)가 설정되지 않아 QR을 만들 수 없어요.")
    buffer = io.BytesIO()
    segno.make(url, error="m", micro=False).save(buffer, kind=image, scale=QR_SCALE, border=4)
    # 링크를 바꾸면 QR도 바뀌어야 해서 저장해 두지 않게 한다
    return Response(buffer.getvalue(), media_type=QR_MEDIA[image], headers={"Cache-Control": "no-store"})


@router.post(
    "/fridges/{fridge_id}/share-link/reset",
    response_model=ShareLinkOut,
    summary="공유 링크 바꾸기 (주인만). 이전 링크와 QR로는 더 들어올 수 없다. 이미 들어온 멤버는 그대로",
)
def reset_share_link(access: FridgeAccess = Depends(require_owner), db: Session = Depends(get_db)):
    access.fridge.share_token = _new_token()
    db.commit()
    return _link(access.fridge.share_token)


# ── 링크로 들어오는 사람 ─────────────────────────────────────────────────


def _fridge_of(db: Session, token: str) -> Fridge:
    fridge = db.scalar(select(Fridge).where(Fridge.share_token == token))
    if fridge is None:
        raise HTTPException(status_code=404, detail=LINK_NOT_FOUND)
    return fridge


@router.get("/share-links/{token}", response_model=ShareLinkPreview, summary="링크를 열었을 때: 누구의 어떤 냉장고인지, 내가 이미 멤버인지")
def preview_link(token: str, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fridge = _fridge_of(db, token)
    return ShareLinkPreview(
        fridge_id=fridge.id,
        fridge_name=fridge.name,
        owner=PublicUser.of(db.get(User, fridge.owner_id)),
        member_count=db.scalar(select(func.count()).select_from(FridgeMember).where(FridgeMember.fridge_id == fridge.id)),
        already_member=member_permission(db, me.id, fridge.id) is not None,
    )


def _membership(db: Session, fridge_id: int, user_id: int) -> FridgeMember | None:
    return db.scalar(select(FridgeMember).where(FridgeMember.fridge_id == fridge_id, FridgeMember.user_id == user_id))


@router.post(
    "/share-links/{token}/join",
    response_model=JoinResult,
    summary="링크로 들어오기: 그 냉장고의 멤버가 되어 주인처럼 고칠 수 있고, 내 냉장고 목록에 들어온다 (이미 멤버면 그대로)",
)
def join_link(token: str, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    fridge = _fridge_of(db, token)
    member = _membership(db, fridge.id, me.id)
    joined = member is None
    if joined:
        db.add(FridgeMember(fridge_id=fridge.id, user_id=me.id, role="member"))
        try:
            db.commit()
        except IntegrityError:  # 같은 사람이 거의 동시에 두 번 눌렀다
            db.rollback()
            joined = False
        member = _membership(db, fridge.id, me.id)
    return JoinResult(fridge=fridge_out(db, fridge, member.role), joined=joined)
