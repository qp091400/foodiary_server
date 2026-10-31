"""회원가입, 로그인, 아이디·비밀번호 찾기 (이메일 인증코드 방식)."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import unauthorized
from app.models import Fridge, FridgeMember, User
from app.schemas import (
    USERNAME_RE,
    USERNAME_RULE,
    FindIdRequest,
    FindIdResponse,
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    ResetPasswordRequest,
    SendCodeRequest,
    SignupRequest,
    TokenPair,
    TokenResponse,
    UsernameCheck,
    VerifyCodeRequest,
    VerifyCodeResponse,
)
from app.security import (
    VERIFY_TOKEN_MINUTES,
    TokenError,
    create_access_token,
    create_refresh_token,
    create_verification_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.services import verification
from app.services.email import send_verification_email

router = APIRouter(prefix="/auth", tags=["인증"])

USERNAME_TAKEN = "이미 사용 중인 아이디입니다."
EMAIL_TAKEN = "이미 가입된 이메일입니다."
# find_id / reset_password는 계정이 있든 없든 이 응답만 준다.
CODE_MAYBE_SENT = "입력한 정보와 일치하는 계정이 있다면 인증코드가 발송됩니다."
# 없는 아이디로 로그인할 때도 bcrypt 검증 시간을 맞추기 위한 더미 해시
_DUMMY_HASH = hash_password("dummy-password-0")


def _user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def _user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def _token_pair(user: User) -> dict[str, str]:
    return {
        "access_token": create_access_token(user),
        "refresh_token": create_refresh_token(user),
        "token_type": "bearer",
    }


@router.get("/check-username", response_model=UsernameCheck, summary="아이디 형식·중복 확인")
def check_username(username: str = Query(max_length=100), db: Session = Depends(get_db)):
    username = username.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        return UsernameCheck(available=False, reason=USERNAME_RULE)
    if _user_by_username(db, username):
        return UsernameCheck(available=False, reason=USERNAME_TAKEN)
    return UsernameCheck(available=True)


@router.post("/email/send-code", response_model=MessageResponse, summary="이메일 인증코드 발송")
def send_code(payload: SendCodeRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    account = _user_by_email(db, payload.email)
    if payload.purpose == "signup":
        if account:
            raise HTTPException(status_code=409, detail=EMAIL_TAKEN)
        deliverable = True
        message = "인증코드를 발송했습니다. 5분 안에 입력해 주세요."
    else:
        deliverable = account is not None
        if payload.purpose == "reset_password":
            deliverable = deliverable and account.username == payload.username
        message = CODE_MAYBE_SENT

    wait = verification.seconds_until_resend(db, payload.email, payload.purpose)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=f"인증코드는 {wait}초 후에 다시 요청할 수 있습니다.",
            headers={"Retry-After": str(wait)},
        )

    code = verification.issue_code(db, payload.email, payload.purpose, deliverable=deliverable)
    db.commit()
    if code:
        background_tasks.add_task(send_verification_email, payload.email, code, payload.purpose)
    return MessageResponse(message=message)


@router.post("/email/verify", response_model=VerifyCodeResponse, summary="인증코드 확인 → 1회용 인증 토큰 발급")
def verify_email(payload: VerifyCodeRequest, db: Session = Depends(get_db)):
    row = verification.verify_code(db, payload.email, payload.purpose, payload.code)
    return VerifyCodeResponse(
        verification_token=create_verification_token(row.id, row.email, row.purpose),
        expires_in=VERIFY_TOKEN_MINUTES * 60,
    )


@router.post("/signup", response_model=TokenResponse, status_code=201, summary="회원가입 (기본 냉장고 자동 생성)")
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    verified = verification.consume_token(db, payload.verification_token, "signup")
    if verified.email != payload.email:
        raise HTTPException(status_code=400, detail="인증한 이메일과 가입하려는 이메일이 다릅니다.")
    if _user_by_username(db, payload.username):
        raise HTTPException(status_code=409, detail=USERNAME_TAKEN)
    if _user_by_email(db, payload.email):
        raise HTTPException(status_code=409, detail=EMAIL_TAKEN)

    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        nickname=payload.nickname,
        household_size=payload.household_size,
    )
    db.add(user)
    try:
        db.flush()
        fridge = Fridge(name=f"{user.nickname}의 냉장고", owner_id=user.id)
        db.add(fridge)
        db.flush()
        db.add(FridgeMember(fridge_id=fridge.id, user_id=user.id, role="owner"))
        db.commit()  # 인증 토큰 소모(consumed_at)도 여기서 함께 확정된다
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디 또는 이메일입니다.") from None
    return {**_token_pair(user), "user": user}


@router.post("/login", response_model=TokenResponse, summary="로그인")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = _user_by_username(db, payload.username)
    password_ok = verify_password(payload.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not password_ok:
        raise unauthorized("아이디 또는 비밀번호가 올바르지 않습니다.")
    return {**_token_pair(user), "user": user}


@router.post("/refresh", response_model=TokenPair, summary="토큰 갱신")
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    invalid = unauthorized("리프레시 토큰이 올바르지 않거나 만료되었습니다. 다시 로그인해 주세요.")
    try:
        claims = decode_token(payload.refresh_token, "refresh")
        user_id = int(claims["sub"])
    except (TokenError, KeyError, ValueError):
        raise invalid from None
    user = db.get(User, user_id)
    if user is None or claims.get("ver") != user.token_version:
        raise invalid
    return _token_pair(user)


@router.post("/find-id", response_model=FindIdResponse, summary="아이디 찾기")
def find_id(payload: FindIdRequest, db: Session = Depends(get_db)):
    verified = verification.consume_token(db, payload.verification_token, "find_id")
    user = _user_by_email(db, verified.email)
    if user is None:
        raise HTTPException(status_code=404, detail="일치하는 계정을 찾을 수 없습니다.")
    db.commit()
    return FindIdResponse(username=user.username, created_at=user.created_at)


@router.post("/reset-password", response_model=MessageResponse, summary="비밀번호 재설정")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    verified = verification.consume_token(db, payload.verification_token, "reset_password")
    user = _user_by_username(db, payload.username)
    if user is None or user.email != verified.email:
        raise HTTPException(status_code=400, detail="인증한 정보와 일치하는 계정이 없습니다.")
    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1  # 기존 토큰 무효화
    db.commit()
    return MessageResponse(message="비밀번호를 변경했습니다. 새 비밀번호로 로그인해 주세요.")
