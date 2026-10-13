from datetime import timedelta

from sqlalchemy import select, update

from app.database import SessionLocal
from app.models import FoodItem, FridgeInvite, FridgeMember
from app.security import INVITE_ALPHABET
from app.timeutil import utcnow
from tests.helpers import auth, default_fridge_id, signup


def _invite(client, tokens, fridge_id) -> str:
    response = client.post(f"/fridges/{fridge_id}/invites", headers=auth(tokens))
    assert response.status_code == 201, response.text
    return response.json()["code"]


def test_signup_creates_default_fridge(client):
    tokens = signup(client, "minsu", nickname="민수")
    fridges = client.get("/fridges", headers=auth(tokens)).json()
    assert len(fridges) == 1
    assert fridges[0]["name"] == "민수의 냉장고"
    assert fridges[0]["my_role"] == "owner"
    assert fridges[0]["member_count"] == 1
    assert fridges[0]["owner_id"] == tokens["user"]["id"]


def test_join_with_invite_code(client):
    owner = signup(client, "owner")
    guest = signup(client, "guest", nickname="손님")
    fridge_id = default_fridge_id(client, owner)

    code = _invite(client, owner, fridge_id)
    assert len(code) == 8 and set(code) <= set(INVITE_ALPHABET)
    assert not set(code) & set("0O1IL")

    # 소문자로 입력해도 된다
    response = client.post("/fridges/join", headers=auth(guest), json={"code": code.lower()})
    assert response.status_code == 200
    assert response.json()["my_role"] == "member"
    assert response.json()["member_count"] == 2

    detail = client.get(f"/fridges/{fridge_id}", headers=auth(guest)).json()
    assert [(m["nickname"], m["role"]) for m in detail["members"]] == [("owner", "owner"), ("손님", "member")]
    assert len(client.get("/fridges", headers=auth(guest)).json()) == 2  # 자기 냉장고 + 참여한 냉장고

    # 이미 멤버면 409, 없는 코드·만료된 코드는 404
    assert client.post("/fridges/join", headers=auth(guest), json={"code": code}).status_code == 409
    assert client.post("/fridges/join", headers=auth(guest), json={"code": "ZZZZ9999"}).status_code == 404
    third = signup(client, "third")
    with SessionLocal() as db:
        db.execute(update(FridgeInvite).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    assert client.post("/fridges/join", headers=auth(third), json={"code": code}).status_code == 404


def test_non_member_gets_404(client):
    owner = signup(client, "owner")
    stranger = signup(client, "stranger")
    fridge_id = default_fridge_id(client, owner)
    headers = auth(stranger)

    assert client.get(f"/fridges/{fridge_id}", headers=headers).status_code == 404
    assert client.patch(f"/fridges/{fridge_id}", headers=headers, json={"name": "뺏기"}).status_code == 404
    assert client.delete(f"/fridges/{fridge_id}", headers=headers).status_code == 404
    assert client.post(f"/fridges/{fridge_id}/invites", headers=headers).status_code == 404
    assert client.get(f"/fridges/{fridge_id}/items", headers=headers).status_code == 404
    assert client.get(f"/fridges/{fridge_id}/receipts", headers=headers).status_code == 404
    assert client.get(f"/fridges/{fridge_id}/stats/monthly", headers=headers).status_code == 404
    assert client.get(f"/fridges/{fridge_id}/purchases", headers=headers).status_code == 404
    # 없는 냉장고와 응답이 같다 (존재 여부 비노출)
    missing = client.get("/fridges/99999", headers=headers)
    assert missing.status_code == 404
    assert missing.json() == client.get(f"/fridges/{fridge_id}", headers=headers).json()
    assert client.get(f"/fridges/{fridge_id}").status_code == 401


def test_leave_and_kick(client):
    owner = signup(client, "owner")
    guest = signup(client, "guest")
    other = signup(client, "other")
    fridge_id = default_fridge_id(client, owner)
    code = _invite(client, owner, fridge_id)
    for tokens in (guest, other):
        assert client.post("/fridges/join", headers=auth(tokens), json={"code": code}).status_code == 200
    owner_id, guest_id, other_id = (tokens["user"]["id"] for tokens in (owner, guest, other))

    # owner 본인은 나갈 수 없다
    response = client.delete(f"/fridges/{fridge_id}/members/{owner_id}", headers=auth(owner))
    assert response.status_code == 400
    assert "삭제" in response.json()["detail"]
    # 멤버는 다른 멤버를 내보낼 수 없다
    assert client.delete(f"/fridges/{fridge_id}/members/{other_id}", headers=auth(guest)).status_code == 403
    # 멤버 나가기
    assert client.delete(f"/fridges/{fridge_id}/members/{guest_id}", headers=auth(guest)).status_code == 204
    assert client.get(f"/fridges/{fridge_id}", headers=auth(guest)).status_code == 404
    # owner가 멤버 내보내기
    assert client.delete(f"/fridges/{fridge_id}/members/{other_id}", headers=auth(owner)).status_code == 204
    assert client.get(f"/fridges/{fridge_id}", headers=auth(other)).status_code == 404
    assert client.delete(f"/fridges/{fridge_id}/members/{other_id}", headers=auth(owner)).status_code == 404
    assert client.get(f"/fridges/{fridge_id}", headers=auth(owner)).json()["member_count"] == 1


def test_owner_only_actions_and_cascade_delete(client):
    owner = signup(client, "owner")
    guest = signup(client, "guest")
    fridge_id = default_fridge_id(client, owner)
    code = _invite(client, owner, fridge_id)
    client.post("/fridges/join", headers=auth(guest), json={"code": code})

    assert client.patch(f"/fridges/{fridge_id}", headers=auth(guest), json={"name": "x"}).status_code == 403
    assert client.delete(f"/fridges/{fridge_id}", headers=auth(guest)).status_code == 403
    renamed = client.patch(f"/fridges/{fridge_id}", headers=auth(owner), json={"name": "우리집"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "우리집"

    # 멤버도 식품을 넣을 수 있다
    assert client.post(f"/fridges/{fridge_id}/items", headers=auth(guest), json={"name": "우유"}).status_code == 201

    assert client.delete(f"/fridges/{fridge_id}", headers=auth(owner)).status_code == 204
    assert client.get(f"/fridges/{fridge_id}", headers=auth(owner)).status_code == 404
    with SessionLocal() as db:
        assert db.scalars(select(FoodItem).where(FoodItem.fridge_id == fridge_id)).all() == []
        assert db.scalars(select(FridgeMember).where(FridgeMember.fridge_id == fridge_id)).all() == []
        assert db.scalars(select(FridgeInvite).where(FridgeInvite.fridge_id == fridge_id)).all() == []


def test_create_second_fridge(client):
    tokens = signup(client, "minsu")
    response = client.post("/fridges", headers=auth(tokens), json={"name": "  회사 냉장고  "})
    assert response.status_code == 201
    assert response.json()["name"] == "회사 냉장고"
    assert response.json()["my_role"] == "owner"
    assert len(client.get("/fridges", headers=auth(tokens)).json()) == 2
