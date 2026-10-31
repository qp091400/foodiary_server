"""Authenticated, explicit per-installation opt-in. No public send endpoint."""
import base64
import hashlib
import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models import Comment, PushDelivery, PushDevice, User
from app.timeutil import utcnow

router = APIRouter(prefix="/notifications/push", tags=["휴대폰 알림"])


def decode_key(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("invalid push key")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class WebKeys(BaseModel):
    p256dh: str = Field(max_length=100)
    auth: str = Field(max_length=30)


class WebSubscription(BaseModel):
    endpoint: str = Field(max_length=2048)
    keys: WebKeys

    @model_validator(mode="after")
    def validate_subscription(self):
        # These are browser push providers, never arbitrary user-controlled URLs.
        url = urlsplit(self.endpoint)
        host = (url.hostname or "").lower()
        allowed = host == "fcm.googleapis.com" or any(host.endswith(suffix) for suffix in (
            ".push.services.mozilla.com", ".push.apple.com", ".notify.windows.com"))
        if not allowed or url.scheme != "https" or url.username or url.password or url.port not in (None, 443) or url.fragment:
            raise ValueError("지원하는 HTTPS 푸시 주소가 아니에요.")
        if len(decode_key(self.keys.auth)) != 16:
            raise ValueError("invalid push auth key")
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decode_key(self.keys.p256dh))
        return self


class DeviceRegistration(BaseModel):
    platform: Literal["web", "expo"]
    subscription: WebSubscription | None = None
    token: str | None = Field(default=None, max_length=200)
    expiry: bool = True
    comments: bool = True

    @model_validator(mode="after")
    def validate_platform(self):
        if self.platform == "web" and not self.subscription:
            raise ValueError("웹 알림 등록 정보가 필요해요.")
        if self.platform == "expo" and not re.fullmatch(r"(?:ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]+\]", self.token or ""):
            raise ValueError("앱 알림 등록 정보가 필요해요.")
        return self


@router.get("/config")
def config(me: User = Depends(get_current_user)):
    return {"web_enabled": bool(settings.push_web_public_key), "public_key": settings.push_web_public_key,
            "native_enabled": settings.push_expo_enabled}


@router.put("/devices/{installation_id}")
def register(installation_id: UUID, payload: DeviceRegistration,
             me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if (payload.platform == "web" and not settings.push_web_public_key) or (payload.platform == "expo" and not settings.push_expo_enabled):
        raise HTTPException(503, "휴대폰 알림을 준비 중이에요. 잠시 후 다시 시도해주세요.")
    credential = payload.subscription.model_dump() if payload.platform == "web" else {"token": payload.token}
    endpoint = credential["endpoint"] if payload.platform == "web" else credential["token"]
    fingerprint = hashlib.sha256(endpoint.encode()).hexdigest()
    key = str(installation_id)
    device = db.get(PushDevice, key)
    duplicate = db.scalar(select(PushDevice).where(PushDevice.fingerprint == fingerprint, PushDevice.id != key))
    if duplicate:
        db.delete(duplicate)
        db.flush()
    baseline = db.scalar(select(func.max(Comment.id))) or 0
    if device is None:
        if (db.scalar(select(func.count()).select_from(PushDevice).where(PushDevice.user_id == me.id)) or 0) >= 10:
            raise HTTPException(409, "등록 가능한 기기 수를 초과했어요.")
        device = PushDevice(id=key, user_id=me.id, token_version=me.token_version, platform=payload.platform,
                            fingerprint=fingerprint, credential=credential, comments_after_id=baseline)
        db.add(device)
    elif device.user_id != me.id or device.token_version != me.token_version or not device.enabled or device.fingerprint != fingerprint:
        db.execute(delete(PushDelivery).where(PushDelivery.device_id == key))
        device.comments_after_id = baseline
        device.created_at = utcnow()
    elif not device.comments and payload.comments:
        device.comments_after_id = baseline
    device.user_id, device.token_version = me.id, me.token_version
    device.platform, device.fingerprint, device.credential = payload.platform, fingerprint, credential
    device.enabled, device.expiry, device.comments = True, payload.expiry, payload.comments
    device.updated_at = utcnow()
    db.commit()
    return {"enabled": True}


@router.delete("/devices/{installation_id}", status_code=204)
def unregister(installation_id: UUID, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    device = db.get(PushDevice, str(installation_id))
    if device and device.user_id == me.id:
        device.enabled = False
        db.commit()
    return Response(status_code=204)
