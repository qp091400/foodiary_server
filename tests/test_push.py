import base64
from datetime import timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import PushDelivery, PushDevice, User
from app.services import push
from app.timeutil import utcnow
from tests.helpers import auth, default_fridge_id, signup


def subscription(endpoint="https://fcm.googleapis.com/fcm/send/test-token"):
    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    return {"endpoint": endpoint, "keys": {"p256dh": encode(point), "auth": encode(b"a" * 16)}}


@pytest.fixture
def world(client, monkeypatch):
    monkeypatch.setattr(settings, "push_web_public_key", "test-key")
    monkeypatch.setattr(settings, "push_expo_enabled", True)
    alice, bob = signup(client, "alice"), signup(client, "bob_kim")
    return {"alice": alice, "bob": bob, "headers": auth(alice), "id": str(uuid4()), "sub": subscription()}


def register(client, world, **extra):
    return client.put("/notifications/push/devices/" + world["id"], headers=world["headers"], json={
        "platform": "web", "subscription": world["sub"], **extra})


def post_comment(client, world, who="bob"):
    post = client.post("/posts", headers=world["headers"], json={"title": "글", "body": "본문"}).json()
    response = client.post(f'/posts/{post["id"]}/comments', headers=auth(world[who]), json={"body": "새 댓글"})
    assert response.status_code == 201
    return response.json()


def run(monkeypatch, *, fail=None, now=None):
    sent = []
    def send(device, payload):
        sent.append((device.user_id, payload))
        if fail:
            raise fail
    monkeypatch.setattr(push, "send", send)
    with SessionLocal() as db:
        result = push.dispatch(db, now=now)
    return result, sent


def test_requires_auth_and_exposes_only_public_config(client, world, monkeypatch):
    assert client.get("/notifications/push/config").status_code == 401
    monkeypatch.setattr(settings, "push_vapid_private_key", "never-return-this")
    response = client.get("/notifications/push/config", headers=world["headers"])
    assert response.json()["public_key"] == "test-key"
    assert "never-return-this" not in response.text
    assert register(client, world).status_code == 200
    monkeypatch.setattr(settings, "push_web_public_key", "")
    assert register(client, world).status_code == 503


@pytest.mark.parametrize("endpoint", ["http://fcm.googleapis.com/fcm/send/x", "https://localhost/x", "https://169.254.169.254/x",
    "https://fcm.googleapis.com.attacker.com/x", "https://fcm.googleapis.com:8080/x", "https://name:secret@fcm.googleapis.com/x"])
def test_rejects_arbitrary_push_endpoints(client, world, endpoint):
    world["sub"]["endpoint"] = endpoint
    assert register(client, world).status_code == 422


def test_key_validation_and_owner_scoped_disable(client, world):
    world["sub"]["keys"]["auth"] = "bad"
    assert register(client, world).status_code == 422
    world["sub"] = subscription()
    assert register(client, world).status_code == 200
    client.delete("/notifications/push/devices/" + world["id"], headers=auth(world["bob"]))
    with SessionLocal() as db:
        assert db.get(PushDevice, world["id"]).enabled
    client.delete("/notifications/push/devices/" + world["id"], headers=world["headers"])
    with SessionLocal() as db:
        assert not db.get(PushDevice, world["id"]).enabled


def test_only_new_comments_and_no_duplicates_or_self_notifications(client, world, monkeypatch):
    post_comment(client, world)
    register(client, world)
    fresh = post_comment(client, world)
    post_comment(client, world, "alice")
    result, sent = run(monkeypatch)
    assert result["sent"] == 1
    assert sent[0][1]["data"]["postId"] == fresh["post_id"]
    assert run(monkeypatch)[0]["sent"] == 0


def test_preferences_and_logout_stop_delivery(client, world, monkeypatch):
    register(client, world, comments=False)
    post_comment(client, world)
    assert run(monkeypatch)[1] == []
    register(client, world, comments=True)
    assert run(monkeypatch)[1] == []  # Opting back in does not flood old comments.
    post_comment(client, world)
    client.delete("/notifications/push/devices/" + world["id"], headers=world["headers"])
    assert run(monkeypatch)[1] == []


def test_temporary_failure_retries_later_and_dead_subscription_is_disabled(client, world, monkeypatch):
    register(client, world)
    post_comment(client, world)
    now = utcnow()
    run(monkeypatch, fail=push.PushError(), now=now)
    assert run(monkeypatch, now=now)[1] == []
    result, sent = run(monkeypatch, now=now + timedelta(minutes=3), fail=push.PushError("web_410", dead=True))
    assert len(sent) == 1
    with SessionLocal() as db:
        assert not db.get(PushDevice, world["id"]).enabled
        assert db.scalar(select(PushDelivery)).status == "failed"


def test_password_reset_stops_old_device_delivery(client, world, monkeypatch):
    register(client, world)
    post_comment(client, world)
    with SessionLocal() as db:
        db.get(User, world["alice"]["user"]["id"]).token_version += 1
        db.commit()
    assert run(monkeypatch)[1] == []


def test_expiry_digest_once_per_day_and_only_current_members(client, world, monkeypatch):
    register(client, world, comments=False)
    fridge_id = default_fridge_id(client, world["alice"])
    # 09:00 in Seoul is 00:00 UTC.
    now = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    day = now.date()
    client.post(f"/fridges/{fridge_id}/items", headers=world["headers"], json={
        "name": "두부", "quantity": 1, "expiry_date": (day + timedelta(days=3)).isoformat()})
    assert run(monkeypatch, now=now - timedelta(minutes=1))[1] == []
    result, sent = run(monkeypatch, now=now)
    assert result["sent"] == 1
    assert sent[0][1]["data"]["fridgeId"] == fridge_id
    assert run(monkeypatch, now=now + timedelta(minutes=1))[1] == []
    world["headers"] = auth(world["bob"])
    register(client, world)  # Same device changes accounts; never sends Alice's fridge to Bob.
    assert run(monkeypatch, now=now + timedelta(days=1))[1] == []


def test_expo_receipts_remove_uninstalled_devices(client, world, monkeypatch):
    register(client, world, platform="expo", token="ExpoPushToken[test_token]")
    post_comment(client, world)
    monkeypatch.setattr(push, "send", lambda device, payload: "ticket-id")
    now = utcnow()
    with SessionLocal() as db:
        push.dispatch(db, now=now)
        assert db.scalar(select(PushDelivery)).status == "accepted"
    monkeypatch.setattr(push, "_expo", lambda path, body: {"ticket-id": {"status": "error", "details": {"error": "DeviceNotRegistered"}}})
    with SessionLocal() as db:
        push.dispatch(db, now=now + timedelta(minutes=16))
        assert not db.get(PushDevice, world["id"]).enabled


def test_web_transport_encrypts_and_signs_payload_without_logging_credentials(client, world, monkeypatch):
    import requests
    private = ec.generate_private_key(ec.SECP256R1())
    raw = private.private_numbers().private_value.to_bytes(32, "big")
    monkeypatch.setattr(settings, "push_vapid_private_key", base64.urlsafe_b64encode(raw).decode().rstrip("="))
    monkeypatch.setattr(settings, "push_vapid_subject", "https://foodiary-app-ef2b5.web.app")
    requests_sent = []
    def post(transport, endpoint, **kwargs):
        assert transport.max_redirects == 0
        requests_sent.append((endpoint, kwargs))
        response = requests.Response()
        response.status_code = 201
        response._content = b""
        return response
    monkeypatch.setattr(requests.Session, "post", post)
    register(client, world)
    post_comment(client, world)
    with SessionLocal() as db:
        assert push.dispatch(db)["sent"] == 1
    assert len(requests_sent) == 1
    endpoint, options = requests_sent[0]
    assert endpoint == world["sub"]["endpoint"]
    assert options["headers"]["authorization"].startswith("vapid ")
    assert options["headers"]["content-encoding"] == "aes128gcm"
    assert isinstance(options["data"], bytes)
    assert "새 댓글".encode() not in options["data"]
