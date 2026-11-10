"""냉장고 공유 링크와 QR: 냉장고마다 링크가 하나 있고, 링크로 들어온 사람은 그 냉장고의 멤버가 되어 주인처럼 고친다. 친구 기능은 없다."""

import io

import pytest
import zxingcpp
from PIL import Image

from app.config import settings
from tests.helpers import auth, default_fridge_id, signup

BASE = "https://foodiary.kr/share/"


@pytest.fixture
def world(client):
    """주인(owner)의 기본 냉장고와, 아직 아무 관계가 없는 guest, other."""
    people = {}
    for name in ("owner", "guest", "other"):
        tokens = signup(client, name)
        people[name] = {"headers": auth(tokens), "id": tokens["user"]["id"], "tokens": tokens}
    return {**people, "fridge_id": default_fridge_id(client, people["owner"]["tokens"])}


def share(client, world, who="owner"):
    response = client.post(f"/fridges/{world['fridge_id']}/share-link", headers=world[who]["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def join(client, world, token, who):
    return client.post(f"/share-links/{token}/join", headers=world[who]["headers"])


def my_fridges(client, world, who):
    return client.get("/fridges", headers=world[who]["headers"]).json()


# ── 링크 ─────────────────────────────────────────────────────────────────


def test_the_share_button_always_gives_the_same_link_and_each_fridge_has_its_own(client, world):
    first = share(client, world)
    assert len(first["token"]) >= 20 and first["url"] is None  # SHARE_LINK_BASE가 없으면 앱이 token으로 주소를 만든다
    assert share(client, world)["token"] == first["token"]  # 다시 눌러도 같은 링크 (에타 시간표 공유처럼)
    second = client.post("/fridges", headers=world["owner"]["headers"], json={"name": "김치냉장고"}).json()["id"]
    other = client.post(f"/fridges/{second}/share-link", headers=world["owner"]["headers"]).json()
    assert other["token"] != first["token"]


def test_the_link_is_a_full_address_when_the_server_knows_the_app_page(client, world, monkeypatch):
    monkeypatch.setattr(settings, "share_link_base", BASE)
    link = share(client, world)
    assert link["url"] == BASE + link["token"]


def test_opening_the_link_shows_whose_fridge_and_joining_puts_it_in_my_list(client, world):
    token = share(client, world)["token"]
    preview = client.get(f"/share-links/{token}", headers=world["guest"]["headers"])
    assert preview.status_code == 200
    body = preview.json()
    assert (body["fridge_id"], body["fridge_name"], body["member_count"], body["already_member"]) == (world["fridge_id"], "owner의 냉장고", 1, False)
    assert set(body["owner"]) == {"id", "nickname", "avatar_url"} and body["owner"]["id"] == world["owner"]["id"]

    joined = join(client, world, token, "guest")
    assert joined.status_code == 200 and joined.json()["joined"] is True
    fridge = joined.json()["fridge"]
    assert (fridge["id"], fridge["name"], fridge["my_role"], fridge["member_count"]) == (world["fridge_id"], "owner의 냉장고", "member", 2)
    listed = {f["id"]: f["my_role"] for f in my_fridges(client, world, "guest")}
    assert listed[world["fridge_id"]] == "member" and len(listed) == 2  # 내 냉장고 + 공유받은 냉장고
    assert client.get(f"/share-links/{token}", headers=world["guest"]["headers"]).json()["already_member"] is True
    members = client.get(f"/fridges/{world['fridge_id']}", headers=world["owner"]["headers"]).json()["members"]
    assert [(m["nickname"], m["role"]) for m in members] == [("owner", "owner"), ("guest", "member")]


def test_a_member_from_the_link_edits_like_the_owner(client, world):
    join(client, world, share(client, world)["token"], "guest")
    items = f"/fridges/{world['fridge_id']}/items"
    added = client.post(items, headers=world["guest"]["headers"], json={"name": "우유", "quantity": 2})
    assert added.status_code == 201
    item = added.json()["id"]
    assert client.patch(f"{items}/{item}", headers=world["guest"]["headers"], json={"memo": "반 남음"}).status_code == 200
    assert client.post(f"{items}/{item}/consume", headers=world["guest"]["headers"], json={"quantity": 1}).status_code == 200
    seen = client.get(items, headers=world["owner"]["headers"]).json()
    assert [(i["name"], i["remaining_quantity"], i["memo"]) for i in seen] == [("우유", 1.0, "반 남음")]  # 주인에게도 바로 보인다
    # 냉장고 지우기, 이름·공개 범위 바꾸기, 링크 바꾸기, 다른 멤버 내보내기는 주인만 한다
    fridge = f"/fridges/{world['fridge_id']}"
    assert client.patch(fridge, headers=world["guest"]["headers"], json={"name": "뺏기"}).status_code == 403
    assert client.delete(fridge, headers=world["guest"]["headers"]).status_code == 403
    assert client.post(f"{fridge}/share-link/reset", headers=world["guest"]["headers"]).status_code == 403
    assert client.delete(f"{fridge}/members/{world['owner']['id']}", headers=world["guest"]["headers"]).status_code == 403


def test_members_can_pass_the_same_link_on(client, world):
    token = share(client, world)["token"]
    join(client, world, token, "guest")
    assert share(client, world, "guest")["token"] == token  # 멤버도 공유 버튼으로 같은 링크를 받는다
    assert join(client, world, token, "other").json()["fridge"]["member_count"] == 3


def test_joining_again_or_opening_my_own_link_changes_nothing(client, world):
    token = share(client, world)["token"]
    assert join(client, world, token, "guest").json()["joined"] is True
    again = join(client, world, token, "guest")
    assert again.status_code == 200 and again.json()["joined"] is False and again.json()["fridge"]["member_count"] == 2
    own = join(client, world, token, "owner")
    assert own.status_code == 200 and own.json()["joined"] is False and own.json()["fridge"]["my_role"] == "owner"
    assert client.get(f"/share-links/{token}", headers=world["owner"]["headers"]).json()["already_member"] is True


def test_resetting_the_link_closes_the_old_one_but_keeps_the_members(client, world):
    old = share(client, world)["token"]
    join(client, world, old, "guest")
    reset = client.post(f"/fridges/{world['fridge_id']}/share-link/reset", headers=world["owner"]["headers"])
    assert reset.status_code == 200 and reset.json()["token"] != old
    assert share(client, world)["token"] == reset.json()["token"]  # 공유 버튼은 이제 새 링크를 준다
    assert client.get(f"/share-links/{old}", headers=world["other"]["headers"]).status_code == 404
    assert join(client, world, old, "other").status_code == 404
    assert client.get(f"/fridges/{world['fridge_id']}", headers=world["guest"]["headers"]).status_code == 200  # 이미 들어온 멤버는 그대로
    assert join(client, world, reset.json()["token"], "other").status_code == 200


def test_a_removed_member_can_come_back_until_the_link_is_reset(client, world):
    token = share(client, world)["token"]
    join(client, world, token, "guest")
    fridge = f"/fridges/{world['fridge_id']}"
    assert client.delete(f"{fridge}/members/{world['guest']['id']}", headers=world["owner"]["headers"]).status_code == 204
    assert client.get(fridge, headers=world["guest"]["headers"]).status_code == 404
    assert join(client, world, token, "guest").json()["joined"] is True  # 링크가 그대로면 다시 들어올 수 있다
    client.delete(f"{fridge}/members/{world['guest']['id']}", headers=world["owner"]["headers"])
    client.post(f"{fridge}/share-link/reset", headers=world["owner"]["headers"])
    assert join(client, world, token, "guest").status_code == 404  # 다시 못 들어오게 하려면 내보낸 뒤 링크를 바꾼다
    # 스스로 나간 멤버도 같다
    join(client, world, share(client, world)["token"], "other")
    assert client.delete(f"{fridge}/members/{world['other']['id']}", headers=world["other"]["headers"]).status_code == 204
    assert world["fridge_id"] not in {f["id"] for f in my_fridges(client, world, "other")}


def test_wrong_links_strangers_and_logged_out_people_are_turned_away(client, world):
    for token in ("없는링크", "x" * 22):
        assert client.get(f"/share-links/{token}", headers=world["guest"]["headers"]).status_code == 404
        assert join(client, world, token, "guest").status_code == 404
    fridge = f"/fridges/{world['fridge_id']}"
    for method, path in (("post", f"{fridge}/share-link"), ("post", f"{fridge}/share-link/reset"), ("get", f"{fridge}/share-link/qr")):
        assert getattr(client, method)(path, headers=world["guest"]["headers"]).status_code == 404  # 멤버가 아니면 링크도 QR도 없다
    token = share(client, world)["token"]
    for method, path in (("get", f"/share-links/{token}"), ("post", f"/share-links/{token}/join"), ("post", f"{fridge}/share-link"), ("get", f"{fridge}/share-link/qr")):
        assert getattr(client, method)(path).status_code == 401


def test_deleting_the_fridge_kills_its_link(client, world):
    token = share(client, world)["token"]
    join(client, world, token, "guest")
    assert client.delete(f"/fridges/{world['fridge_id']}", headers=world["owner"]["headers"]).status_code == 204
    assert join(client, world, token, "other").status_code == 404
    assert [f["my_role"] for f in my_fridges(client, world, "guest")] == ["owner"]  # guest에게는 자기 냉장고만 남는다


# ── QR ───────────────────────────────────────────────────────────────────


def qr(client, world, who="owner", **params):
    return client.get(f"/fridges/{world['fridge_id']}/share-link/qr", params=params, headers=world[who]["headers"])


def read_qr(png: bytes) -> list[str]:
    return [result.text for result in zxingcpp.read_barcodes(Image.open(io.BytesIO(png)))]


def test_the_qr_opens_the_same_link_as_the_share_button(client, world, monkeypatch):
    monkeypatch.setattr(settings, "share_link_base", BASE)
    response = qr(client, world)
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"  # 링크를 바꾸면 QR도 바뀌어야 한다
    assert read_qr(response.content) == [BASE + share(client, world)["token"]]  # 휴대폰 카메라로 찍으면 이 주소가 열린다
    svg = qr(client, world, format="svg")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml") and b"<svg" in svg.content


def test_the_qr_makes_the_link_when_there_is_none_yet_and_follows_a_reset(client, world, monkeypatch):
    monkeypatch.setattr(settings, "share_link_base", BASE)
    first = read_qr(qr(client, world).content)  # 공유 버튼을 누르기 전에 QR부터 열어도 된다
    assert first == [BASE + share(client, world)["token"]]
    new = client.post(f"/fridges/{world['fridge_id']}/share-link/reset", headers=world["owner"]["headers"]).json()["token"]
    assert read_qr(qr(client, world).content) == [BASE + new]
    join(client, world, new, "guest")
    assert read_qr(qr(client, world, "guest").content) == [BASE + new]  # 멤버도 같은 QR을 보여 줄 수 있다


def test_without_the_app_address_the_server_cannot_make_a_qr(client, world):
    response = qr(client, world)
    assert response.status_code == 503 and "SHARE_LINK_BASE" in response.json()["detail"]
    assert qr(client, world, format="gif").status_code == 422


# ── 없어진 친구 기능 ─────────────────────────────────────────────────────


def test_friend_features_are_gone(client, world):
    headers, fridge = world["owner"]["headers"], world["fridge_id"]
    for method, path in (
        ("get", "/friends"),
        ("post", "/friends"),
        ("post", "/change-requests"),
        ("get", "/change-requests/received"),
        ("post", f"/fridges/{fridge}/invites"),
        ("post", "/fridges/join"),
        ("post", f"/fridges/{fridge}/share-links"),
        ("get", f"/fridges/{fridge}/access"),
        ("get", "/notifications/change-results"),
    ):
        assert getattr(client, method)(path, headers=headers).status_code in (404, 405), path
    assert "friend_code" not in client.get("/users/me", headers=headers).json()
