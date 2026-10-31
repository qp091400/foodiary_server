"""bcrypt, JWT, 인증코드 생성."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.config import settings
from app.models import User

ALGORITHM = "HS256"
VERIFY_TOKEN_MINUTES = 15
BCRYPT_ROUNDS = 12


class TokenError(Exception):
    """서명·만료·종류가 맞지 않는 토큰."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:  # 72바이트 초과 등 bcrypt가 받지 못하는 입력
        return False


def _encode(claims: dict[str, Any], expires_in: timedelta, token_type: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {**claims, "type": token_type, "iat": now, "exp": now + expires_in}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def create_access_token(user: User) -> str:
    claims = {"sub": str(user.id), "ver": user.token_version}
    return _encode(claims, timedelta(minutes=settings.access_token_minutes), "access")


def create_refresh_token(user: User) -> str:
    claims = {"sub": str(user.id), "ver": user.token_version}
    return _encode(claims, timedelta(days=settings.refresh_token_days), "refresh")


def create_verification_token(verification_id: int, email: str, purpose: str) -> str:
    claims = {"vid": verification_id, "email": email, "purpose": purpose}
    return _encode(claims, timedelta(minutes=VERIFY_TOKEN_MINUTES), "verify")


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    """서명·만료를 검증하고, type 클레임이 다르면 거부한다."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != expected_type:
        raise TokenError("token type mismatch")
    return payload


def generate_verification_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def hash_verification_code(email: str, purpose: str, code: str) -> str:
    message = f"{email}:{purpose}:{code}".encode("utf-8")
    return hmac.new(settings.jwt_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

