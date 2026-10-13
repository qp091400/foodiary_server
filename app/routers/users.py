"""내 정보."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import PasswordChangeRequest, PasswordChangeResponse, UserOut, UserUpdate
from app.security import create_access_token, create_refresh_token, hash_password, verify_password

router = APIRouter(prefix="/users", tags=["사용자"])


@router.get("/me", response_model=UserOut, summary="내 정보")
def get_me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserOut, summary="닉네임 변경")
def update_me(payload: UserUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.nickname = payload.nickname
    db.commit()
    return user


@router.post("/me/password", response_model=PasswordChangeResponse, summary="비밀번호 변경")
def change_password(
    payload: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="새 비밀번호가 현재 비밀번호와 같습니다.")
    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1  # 다른 기기의 토큰은 여기서 모두 무효가 된다
    db.commit()
    # 지금 쓰던 토큰도 무효가 됐으니, 이 기기는 새 토큰으로 로그인 상태를 이어 간다.
    return PasswordChangeResponse(
        message="비밀번호를 변경했습니다.",
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
    )
