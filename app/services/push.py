"""Durable, opt-in push delivery. Called by the private scheduled Cloud Run job."""
import json
import logging
import time
from datetime import timedelta, timezone, datetime
from zoneinfo import ZoneInfo

import httpx
import requests
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Comment, FoodItem, Fridge, FridgeMember, Post, PushDelivery, PushDevice, User
from app.timeutil import utcnow

log = logging.getLogger(__name__)
EXPO_API = "https://exp.host/--/api/v2/push/"


class PushError(Exception):
    def __init__(self, code="temporary", *, dead=False, permanent=False):
        self.code, self.dead, self.permanent = code, dead, permanent


def _expo(path, body):
    headers = {"Authorization": "Bearer " + settings.push_expo_access_token} if settings.push_expo_access_token else {}
    try:
        response = httpx.post(EXPO_API + path, json=body, headers=headers, timeout=10)
        if response.status_code in (400, 401, 403):
            raise PushError("expo_configuration", permanent=True)
        response.raise_for_status()
        return response.json()["data"]
    except (httpx.HTTPError, ValueError, KeyError):
        raise PushError() from None


def _expo_error(data):
    code = data.get("details", {}).get("error", "expo_error")
    raise PushError(code, dead=code == "DeviceNotRegistered",
                    permanent=code in ("MessageTooBig", "InvalidCredentials", "MismatchSenderId"))


def send(device: PushDevice, payload: dict) -> str | None:
    if device.platform == "web":
        if not settings.push_vapid_private_key or not settings.push_vapid_subject:
            raise PushError("web_configuration", permanent=True)
        try:
            with requests.Session() as transport:
                transport.max_redirects = 0
                webpush(subscription_info=device.credential, data=json.dumps(payload, ensure_ascii=False),
                        vapid_private_key=settings.push_vapid_private_key, requests_session=transport,
                        vapid_claims={"sub": settings.push_vapid_subject}, ttl=3600, timeout=10)
        except WebPushException as error:
            status = error.response.status_code if error.response is not None else 0
            raise PushError("web_" + str(status), dead=status in (404, 410), permanent=status in (400, 401, 403, 413)) from None
        return None
    data = _expo("send", {"to": device.credential["token"], "title": payload["title"], "body": payload["body"],
                          "sound": "default", "channelId": "foodiary", "ttl": 3600,
                          "data": payload["data"]})
    if data.get("status") != "ok":
        _expo_error(data)
    return data["id"]


def _expiry_items(db, user_id, fridge_id, day):
    return db.scalars(select(FoodItem).join(FridgeMember, FridgeMember.fridge_id == FoodItem.fridge_id).where(
        FridgeMember.user_id == user_id, FoodItem.fridge_id == fridge_id,
        FoodItem.status == "active", FoodItem.remaining_quantity > 0,
        FoodItem.expiry_date >= day, FoodItem.expiry_date <= day + timedelta(days=3)
    ).order_by(FoodItem.expiry_date, FoodItem.id)).all()


def _payload(db, device, event, now):
    user = db.get(User, device.user_id)
    if not device.enabled or not user or user.token_version != device.token_version:
        return None
    if event.kind == "comment":
        comment = db.get(Comment, event.target_id)
        post = db.get(Post, comment.post_id) if comment else None
        if not device.comments or not comment or comment.deleted_at or not post or post.deleted_at or post.author_id != user.id or comment.author_id == user.id or comment.id <= user.comments_seen_id:
            return None
        author = db.get(User, comment.author_id)
        return {"title": f"{author.nickname if author else '사용자'}님이 댓글을 남겼어요", "body": comment.body[:120],
                "tag": event.event_key, "data": {"kind": "comment", "postId": post.id, "userId": user.id},
                "url": f"/community?postId={post.id}"}
    day = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(settings.timezone)).date()
    if not device.expiry or event.event_key != f"expiry:{day}:{event.target_id}":
        return None
    items = _expiry_items(db, user.id, event.target_id, day)
    fridge = db.get(Fridge, event.target_id)
    if not items or not fridge:
        return None
    return {"title": f"{fridge.name} · 소비기한 알림", "body": f"{items[0].name} 등 {len(items)}개 식품의 소비기한이 3일 이내예요.",
            "tag": event.event_key, "data": {"kind": "expiry", "fridgeId": fridge.id, "userId": user.id},
            "url": f"/?pushFridgeId={fridge.id}&pushUserId={user.id}"}


def _enqueue(db, device, now):
    events = []
    if device.comments:
        comments = db.scalars(select(Comment).join(Post, Post.id == Comment.post_id).join(User, User.id == Post.author_id).where(
            Post.author_id == device.user_id, Post.deleted_at.is_(None), Comment.deleted_at.is_(None),
            Comment.author_id != device.user_id, Comment.id > device.comments_after_id,
            Comment.id > User.comments_seen_id, Comment.created_at >= now - timedelta(days=1)
        ).order_by(Comment.id).limit(30)).all()
        events += [(f"comment:{c.id}", "comment", c.id) for c in comments]
        if comments:
            device.comments_after_id = comments[-1].id
    local = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(settings.timezone))
    if device.expiry and local.hour >= 9:
        ids = db.scalars(select(FridgeMember.fridge_id).where(FridgeMember.user_id == device.user_id)).all()
        events += [(f"expiry:{local.date()}:{id}", "expiry", id) for id in ids if _expiry_items(db, device.user_id, id, local.date())]
    for key, kind, target in events:
        if db.scalar(select(PushDelivery.id).where(PushDelivery.device_id == device.id, PushDelivery.event_key == key)) is None:
            db.add(PushDelivery(device_id=device.id, event_key=key, kind=kind, target_id=target,
                                created_at=now, next_attempt_at=now))
    db.commit()


def _failure(device, event, error, now):
    event.error_code = error.code[:50]
    if error.dead:
        device.enabled = False
    event.status = "failed" if error.dead or error.permanent or event.attempts >= 5 else "pending"
    event.next_attempt_at = now + timedelta(seconds=min(3600, 60 * 2 ** event.attempts))
    # Never log endpoints, tokens, subscription keys or notification bodies.
    log.warning("Push delivery %s: %s", event.id, event.error_code)


def dispatch(db: Session, now=None, budget_seconds=40):
    now = now or utcnow()
    deadline = time.monotonic() + budget_seconds
    sent, skipped = 0, 0
    # Expired credentials are never sent after a password reset, nor indefinitely.
    devices = db.scalars(select(PushDevice).join(User, User.id == PushDevice.user_id).where(
        PushDevice.enabled.is_(True), PushDevice.token_version == User.token_version,
        PushDevice.updated_at >= now - timedelta(days=90)
    ).order_by(PushDevice.updated_at)).all()
    for device in devices:
        if time.monotonic() >= deadline:
            break
        receipts = db.scalars(select(PushDelivery).where(PushDelivery.device_id == device.id,
            PushDelivery.status == "accepted", PushDelivery.next_attempt_at <= now).limit(100)).all()
        if receipts:
            try:
                results = _expo("getReceipts", {"ids": [event.ticket for event in receipts]})
                for event in receipts:
                    result = results.get(event.ticket)
                    if result and result.get("status") == "ok":
                        event.status = "sent"
                    elif result:
                        try:
                            _expo_error(result)
                        except PushError as error:
                            _failure(device, event, error, now)
                    elif event.created_at < now - timedelta(days=1):
                        event.status, event.error_code = "failed", "receipt_expired"
                    else:
                        event.next_attempt_at = now + timedelta(minutes=15)
                db.commit()
            except PushError:
                pass  # Retry receipt lookup next minute; never resend an accepted ticket.
        if not device.enabled:
            continue
        _enqueue(db, device, now)
        pending = db.scalars(select(PushDelivery).where(PushDelivery.device_id == device.id,
            PushDelivery.status == "pending", PushDelivery.next_attempt_at <= now).order_by(PushDelivery.id).limit(30)).all()
        for event in pending:
            if time.monotonic() >= deadline:
                break
            # Refresh the opt-in and access checks immediately before sending.
            db.refresh(device)
            payload = _payload(db, device, event, now)
            if payload is None:
                event.status = "skipped"
                skipped += 1
                db.commit()
                continue
            event.attempts += 1
            event.next_attempt_at = now + timedelta(minutes=5)
            db.commit()  # Durable retry lease before making the external request.
            try:
                ticket = send(device, payload)
                event.ticket, event.status = ticket, "accepted" if ticket else "sent"
                event.next_attempt_at = now + timedelta(minutes=15)
                sent += 1
            except PushError as error:
                _failure(device, event, error, now)
            except Exception:
                _failure(device, event, PushError("transport"), now)
            db.commit()
    db.execute(delete(PushDelivery).where(PushDelivery.created_at < now - timedelta(days=30)))
    db.commit()
    return {"sent": sent, "skipped": skipped}
