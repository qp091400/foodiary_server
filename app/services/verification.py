"""이메일 인증코드 발급·검증, 인증 토큰(verification JWT) 1회 사용 처리."""

import hmac
import math
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import EmailVerification
from app.security import (
    TokenError,
    decode_token,
    generate_verification_code,
    hash_verification_code,
)
from app.timeutil import utcnow

CODE_TTL = timedelta(minutes=5)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_ATTEMPTS = 5

_INVALID_TOKEN = "인증 토큰이 올바르지 않거나 만료되었습니다. 이메일 인증을 다시 해 주세요."
_TOO_MANY_ATTEMPTS = "인증 시도 횟수를 초과했습니다. 인증코드를 다시 요청해 주세요."


def _latest(db: Session, email: str, purpose: str) -> EmailVerification | None:
    return db.scalar(
        select(EmailVerification)
        .where(EmailVerification.email == email, EmailVerification.purpose == purpose)
        .order_by(EmailVerification.id.desc())
        .limit(1)
    )


def seconds_until_resend(db: Session, email: str, purpose: str) -> int:
    """같은 email+purpose로 다시 보낼 수 있을 때까지 남은 초. 바로 보낼 수 있으면 0."""
    latest = _latest(db, email, purpose)
    if latest is None:
        return 0
    remaining = (latest.created_at + RESEND_COOLDOWN - utcnow()).total_seconds()
    return max(0, math.ceil(remaining))


def issue_code(db: Session, email: str, purpose: str, *, deliverable: bool) -> str | None:
    """인증코드 행을 만든다 (commit은 호출한 쪽에서).

    deliverable=False(계정 없음)면 절대 맞출 수 없는 해시로 행만 남긴다. 계정이 있을 때와
    재발송 제한·오입력 응답이 똑같아져서 계정 존재 여부가 드러나지 않는다.
    """
    now = utcnow()
    # 인증 토큰 유효시간(15분)까지 지난 옛 행은 정리한다.
    db.execute(
        delete(EmailVerification).where(
            EmailVerification.email == email,
            EmailVerification.purpose == purpose,
            EmailVerification.expires_at < now - timedelta(hours=1),
        )
    )
    code = generate_verification_code() if deliverable else None
    db.add(
        EmailVerification(
            email=email,
            purpose=purpose,
            code_hash=hash_verification_code(email, purpose, code) if code else secrets.token_hex(32),
            expires_at=now + CODE_TTL,
            created_at=now,
        )
    )
    return code


def verify_code(db: Session, email: str, purpose: str, code: str) -> EmailVerification:
    """가장 최근에 발급한 코드와 대조한다. 틀리면 시도 횟수를 올리고 400."""
    row = _latest(db, email, purpose)
    if row is None or row.consumed_at is not None:
        raise HTTPException(status_code=400, detail="인증코드를 먼저 요청해 주세요.")
    if row.expires_at < utcnow():
        raise HTTPException(status_code=400, detail="인증코드가 만료되었습니다. 다시 요청해 주세요.")
    if row.attempts >= MAX_ATTEMPTS:
        raise HTTPException(status_code=400, detail=_TOO_MANY_ATTEMPTS)

    if not hmac.compare_digest(row.code_hash, hash_verification_code(email, purpose, code)):
        row.attempts += 1
        db.commit()
        remaining = MAX_ATTEMPTS - row.attempts
        if remaining <= 0:
            raise HTTPException(status_code=400, detail=_TOO_MANY_ATTEMPTS)
        raise HTTPException(status_code=400, detail=f"인증코드가 올바르지 않습니다. (남은 시도 {remaining}회)")

    if row.verified_at is None:
        row.verified_at = utcnow()
        db.commit()
    return row


def consume_token(db: Session, token: str, purpose: str) -> EmailVerification:
    """인증 토큰을 1회용으로 소모한다.

    consumed_at만 기록하고 commit하지 않는다. 호출한 쪽이 실제 작업(가입 등)과 같은 트랜잭션에서
    commit해야 하며, 작업이 실패하면 함께 롤백되어 토큰을 다시 쓸 수 있다.
    """
    try:
        payload = decode_token(token, "verify")
        verification_id = int(payload["vid"])
    except (TokenError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail=_INVALID_TOKEN) from None
    if payload.get("purpose") != purpose:
        raise HTTPException(status_code=400, detail=_INVALID_TOKEN)

    row = db.get(EmailVerification, verification_id, with_for_update=True)
    if (
        row is None
        or row.email != payload.get("email")
        or row.purpose != purpose
        or row.verified_at is None
    ):
        raise HTTPException(status_code=400, detail=_INVALID_TOKEN)
    if row.consumed_at is not None:
        raise HTTPException(status_code=400, detail="이미 사용한 인증 토큰입니다. 이메일 인증을 다시 해 주세요.")
    row.consumed_at = utcnow()
    return row
