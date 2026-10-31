from sqlalchemy import select

from app.database import SessionLocal
from app.models import FoodItem, FridgeMember
from tests.helpers import auth, default_fridge_id, signup


def _join(client, owner, guest, fridge_id) -> None:
    """주인이 공유 링크를 받아 보내고, guest가 그 링크로 들어온다. (자세한 규칙은 test_fridge_sharing.py)"""
    token = client.post(f"/fridges/{fridge_id}/share-link", headers=auth(owner)).json()["token"]
    response = client.post(f"/share-links/{token}/join", headers=auth(guest))
    assert response.status_code == 200, response.text


def test_signup_creates_default_fridge(client):
    tokens = signup(client, "minsu", nickname="민수")
    fridges = client.get("/fridges", headers=auth(tokens)).json()
    assert len(fridges) == 1
    assert fridges[0]["name"] == "민수의 냉장고"
    assert fridges[0]["my_role"] == "owner"
    assert fridges[0]["member_count"] == 1
    assert fridges[0]["owner_id"] == tokens["user"]["id"]


def test_join_with_share_link(client):
    owner = signup(client, "owner")
    guest = signup(client, "guest", nickname="손님")
    fridge_id = default_fridge_id(client, owner)
    _join(client, owner, guest, fridge_id)

    detail = client.get(f"/fridges/{fridge_id}", headers=auth(guest)).json()
    assert detail["my_role"] == "member" and detail["member_count"] == 2
    assert [(m["nickname"], m["role"]) for m in detail["members"]] == [("owner", "owner"), ("손님", "member")]
    assert len(client.get("/fridges", headers=auth(guest)).json()) == 2  # 자기 냉장고 + 참여한 냉장고


def test_non_member_gets_404(client):
    owner = signup(client, "owner")
    stranger = signup(client, "stranger")
    fridge_id = default_fridge_id(client, owner)
    headers = auth(stranger)

    assert client.get(f"/fridges/{fridge_id}", headers=headers).status_code == 404
    assert client.patch(f"/fridges/{fridge_id}", headers=headers, json={"name": "뺏기"}).status_code == 404
    assert client.delete(f"/fridges/{fridge_id}", headers=headers).status_code == 404
    assert client.post(f"/fridges/{fridge_id}/share-link", headers=headers).status_code == 404
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
    for tokens in (guest, other):
        _join(client, owner, tokens, fridge_id)
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
    _join(client, owner, guest, fridge_id)

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


def test_create_second_fridge(client):
    tokens = signup(client, "minsu")
    response = client.post("/fridges", headers=auth(tokens), json={"name": "  회사 냉장고  "})
    assert response.status_code == 201
    assert response.json()["name"] == "회사 냉장고"
    assert response.json()["my_role"] == "owner"
    assert len(client.get("/fridges", headers=auth(tokens)).json()) == 2
