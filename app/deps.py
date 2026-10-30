"""공통 의존성: 로그인 사용자, 냉장고 접근 권한."""

from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Fridge, FridgeMember, User
from app.security import TokenError, decode_token

bearer_scheme = HTTPBearer(auto_error=False, description="로그인으로 받은 access_token")

FRIDGE_NOT_FOUND = "냉장고를 찾을 수 없습니다."


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise unauthorized("로그인이 필요합니다.")
    try:
        payload = decode_token(credentials.credentials, "access")
        user_id = int(payload["sub"])
    except (TokenError, KeyError, ValueError):
        raise unauthorized("토큰이 올바르지 않거나 만료되었습니다.") from None
    user = db.get(User, user_id)
    # 비밀번호가 바뀌면 token_version이 올라가 이전 토큰은 바로 거부된다.
    if user is None or payload.get("ver") != user.token_version:
        raise unauthorized("토큰이 올바르지 않거나 만료되었습니다.")
    return user


@dataclass
class FridgeAccess:
    fridge: Fridge
    member: FridgeMember
    user: User

    @property
    def is_owner(self) -> bool:
        return self.member.role == "owner"


def get_fridge_access(
    fridge_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FridgeAccess:
    """멤버가 아니면 404 (냉장고 존재 여부를 노출하지 않는다)."""
    member = db.scalar(
        select(FridgeMember).where(FridgeMember.fridge_id == fridge_id, FridgeMember.user_id == user.id)
    )
    fridge = db.get(Fridge, fridge_id) if member else None
    if member is None or fridge is None:
        raise HTTPException(status_code=404, detail=FRIDGE_NOT_FOUND)
    return FridgeAccess(fridge=fridge, member=member, user=user)


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="관리자만 할 수 있는 작업입니다.")
    return user


def require_owner(access: FridgeAccess = Depends(get_fridge_access)) -> FridgeAccess:
    if not access.is_owner:
        raise HTTPException(status_code=403, detail="냉장고 주인만 할 수 있는 작업입니다.")
    return access
