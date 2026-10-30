"""변경 요청: 보기 권한이 있는 친구가 주인에게 "먹었어요", "두고 갔어요"를 보내고, 주인이 승인하면 냉장고에 반영된다."""

import pytest

from app.database import SessionLocal
from app.models import ConsumptionLog, FoodItem
from app.routers import change_requests
from tests.helpers import auth, default_fridge_id, signup


@pytest.fixture
def world(client):
    """주인 alice(우유 2개, 두부), 보기 친구 bob, 고치기 친구 carol, 친구가 아닌 dave."""
    people = {}
    for username, name in (("alice", "alice"), ("bob_kim", "bob"), ("carol", "carol"), ("dave_x", "dave")):
        tokens = signup(client, username, nickname=name)
        people[name] = {"headers": auth(tokens), "id": tokens["user"]["id"], "tokens": tokens}
    alice = people["alice"]
    fridge_id = default_fridge_id(client, alice["tokens"])
    for name, quantity in (("서울우유 1L", 2), ("두부", 1)):
        assert client.post(f"/fridges/{fridge_id}/items", headers=alice["headers"], json={"name": name, "quantity": quantity}).status_code == 201

    def link(permission):
        return client.post(f"/fridges/{fridge_id}/share-links", headers=alice["headers"], json={"permission": permission}).json()["token"]

    assert client.post(f"/share-links/{link('viewer')}/join", headers=people["bob"]["headers"]).status_code == 200
    assert client.post(f"/share-links/{link('editor')}/join", headers=people["carol"]["headers"]).status_code == 200
    items = {i["name"]: i for i in client.get(f"/fridges/{fridge_id}/items", headers=alice["headers"]).json()}
    return {**people, "fridge_id": fridge_id, "milk": items["서울우유 1L"]["id"], "tofu": items["두부"]["id"]}


def send(client, who, world, **body):
    return client.post("/change-requests", headers=who["headers"], json={"fridge_id": world["fridge_id"], **body})


def received(client, world, **params):
    return client.get("/change-requests/received", params=params, headers=world["alice"]["headers"]).json()


def item(client, world, item_id):
    return client.get(f"/fridges/{world['fridge_id']}/items/{item_id}", headers=world["alice"]["headers"]).json()


def test_a_friend_asks_and_the_owner_approves_an_eaten_item(client, world):
    sent = send(client, world["bob"], world, kind="consume", item_id=world["milk"], quantity=1, note="놀러 와서 마셨어요")
    assert sent.status_code == 201
    body = sent.json()
    assert (body["status"], body["kind"], body["item_name"], body["quantity"], body["note"]) == ("pending", "consume", "서울우유 1L", 1, "놀러 와서 마셨어요")
    assert body["requester"]["nickname"] == "bob" and body["fridge_id"] == world["fridge_id"]
    assert item(client, world, world["milk"])["remaining_quantity"] == 2  # 승인하기 전에는 그대로
    assert [r["id"] for r in received(client, world)] == [body["id"]]
    approved = client.post(f"/change-requests/{body['id']}/approve", headers=world["alice"]["headers"])
    assert approved.status_code == 200 and approved.json()["status"] == "approved" and approved.json()["handled_at"]
    assert item(client, world, world["milk"])["remaining_quantity"] == 1
    with SessionLocal() as db:  # 누가 먹었는지는 요청한 사람으로 남는다
        assert db.query(ConsumptionLog).filter_by(food_item_id=world["milk"]).one().user_id == world["bob"]["id"]
    assert client.post(f"/change-requests/{body['id']}/approve", headers=world["alice"]["headers"]).status_code == 409  # 두 번 반영되지 않는다
    assert received(client, world) == []
    assert [r["status"] for r in received(client, world, status="all")] == ["approved"]
    assert [r["status"] for r in client.get("/change-requests/sent", headers=world["bob"]["headers"]).json()] == ["approved"]


def test_eaten_without_a_quantity_means_all_that_is_left(client, world):
    request_id = send(client, world["bob"], world, kind="consume", item_id=world["tofu"]).json()["id"]
    client.post(f"/change-requests/{request_id}/approve", headers=world["alice"]["headers"])
    assert item(client, world, world["tofu"])["status"] == "consumed"


def test_a_friend_leaves_something_and_the_owner_adds_it(client, world):
    sent = send(client, world["bob"], world, kind="add", name="엄마 반찬", quantity=3, storage="freezer", expiry_date="2099-03-01", note="부모님이 두고 가셨어요")
    assert sent.status_code == 201 and sent.json()["item_name"] == "엄마 반찬" and sent.json()["item_id"] is None
    items_before = len(client.get(f"/fridges/{world['fridge_id']}/items", headers=world["alice"]["headers"]).json())
    client.post(f"/change-requests/{sent.json()['id']}/approve", headers=world["alice"]["headers"])
    items = client.get(f"/fridges/{world['fridge_id']}/items", headers=world["alice"]["headers"]).json()
    assert len(items) == items_before + 1
    added = next(i for i in items if i["name"] == "엄마 반찬")
    assert (added["quantity"], added["storage"], added["expiry_date"], added["memo"], added["added_by"]) == (3, "freezer", "2099-03-01", "부모님이 두고 가셨어요", world["bob"]["id"])


def test_a_discard_request(client, world):
    request_id = send(client, world["bob"], world, kind="discard", item_id=world["milk"], quantity=2).json()["id"]
    client.post(f"/change-requests/{request_id}/approve", headers=world["alice"]["headers"])
    assert item(client, world, world["milk"])["status"] == "discarded"


def test_declining_changes_nothing(client, world):
    request_id = send(client, world["bob"], world, kind="consume", item_id=world["milk"], quantity=1).json()["id"]
    declined = client.post(f"/change-requests/{request_id}/decline", headers=world["alice"]["headers"])
    assert declined.status_code == 200 and declined.json()["status"] == "declined"
    assert item(client, world, world["milk"])["remaining_quantity"] == 2
    assert client.post(f"/change-requests/{request_id}/approve", headers=world["alice"]["headers"]).status_code == 409
    assert client.post(f"/change-requests/{request_id}/decline", headers=world["alice"]["headers"]).status_code == 409
    assert [r["status"] for r in client.get("/change-requests/sent", headers=world["bob"]["headers"]).json()] == ["declined"]


def test_the_sender_can_cancel_while_it_is_pending(client, world):
    request_id = send(client, world["bob"], world, kind="consume", item_id=world["milk"]).json()["id"]
    assert client.delete(f"/change-requests/{request_id}", headers=world["carol"]["headers"]).status_code == 404  # 남의 요청
    assert client.delete(f"/change-requests/{request_id}", headers=world["alice"]["headers"]).status_code == 404  # 받은 사람은 취소가 아니라 거절
    assert client.delete(f"/change-requests/{request_id}", headers=world["bob"]["headers"]).status_code == 204
    assert received(client, world) == []
    assert [r["status"] for r in client.get("/change-requests/sent", params={"status": "cancelled"}, headers=world["bob"]["headers"]).json()] == ["cancelled"]
    assert client.post(f"/change-requests/{request_id}/approve", headers=world["alice"]["headers"]).status_code == 409
    assert client.delete(f"/change-requests/{request_id}", headers=world["bob"]["headers"]).status_code == 404  # 이미 취소했다


def test_only_the_owner_handles_requests(client, world):
    request_id = send(client, world["bob"], world, kind="consume", item_id=world["milk"]).json()["id"]
    for who in ("bob", "carol", "dave"):  # 고치기 권한이 있는 친구도 요청은 처리하지 못한다
        assert client.post(f"/change-requests/{request_id}/approve", headers=world[who]["headers"]).status_code == 404
        assert client.post(f"/change-requests/{request_id}/decline", headers=world[who]["headers"]).status_code == 404
        assert client.get("/change-requests/received", headers=world[who]["headers"]).json() == []
    assert client.post("/change-requests/99999/approve", headers=world["alice"]["headers"]).status_code == 404


def test_approving_fails_if_the_item_is_already_gone_and_the_request_stays_open(client, world):
    request_id = send(client, world["bob"], world, kind="consume", item_id=world["milk"]).json()["id"]
    client.post(f"/fridges/{world['fridge_id']}/items/{world['milk']}/consume", headers=world["alice"]["headers"], json={})  # 그 사이 주인이 먹었다
    failed = client.post(f"/change-requests/{request_id}/approve", headers=world["alice"]["headers"])
    assert failed.status_code == 400
    assert [r["status"] for r in received(client, world)] == ["pending"]  # 처리 전으로 남는다
    assert client.post(f"/change-requests/{request_id}/decline", headers=world["alice"]["headers"]).status_code == 200


def test_request_content_is_checked(client, world):
    milk = world["milk"]
    assert send(client, world["bob"], world, kind="add").status_code == 422  # 이름이 없다
    assert send(client, world["bob"], world, kind="add", name="x", item_id=milk).status_code == 422
    assert send(client, world["bob"], world, kind="consume").status_code == 422  # 품목이 없다
    assert send(client, world["bob"], world, kind="consume", item_id=milk, name="x").status_code == 422
    assert send(client, world["bob"], world, kind="consume", item_id=milk, quantity=0).status_code == 422
    assert send(client, world["bob"], world, kind="move", item_id=milk).status_code == 422
    assert send(client, world["bob"], world, kind="consume", item_id=milk, quantity=3).status_code == 400  # 남은 것보다 많다
    assert send(client, world["bob"], world, kind="consume", item_id=99999).status_code == 404
    assert send(client, world["bob"], world, kind="consume", item_id=milk, note="가" * 201).status_code == 422
    other = default_fridge_id(client, world["bob"]["tokens"])  # 다른 냉장고의 품목은 이 냉장고 요청에 쓸 수 없다
    foreign = client.post(f"/fridges/{other}/items", headers=world["bob"]["headers"], json={"name": "내 우유"}).json()["id"]
    assert send(client, world["bob"], world, kind="consume", item_id=foreign).status_code == 404
    client.post(f"/fridges/{world['fridge_id']}/items/{milk}/consume", headers=world["alice"]["headers"], json={})
    assert send(client, world["bob"], world, kind="consume", item_id=milk).status_code == 404  # 이미 먹은 품목


def test_who_may_send(client, world):
    milk = world["milk"]
    assert send(client, world["carol"], world, kind="consume", item_id=milk).status_code == 400  # 고칠 수 있으니 직접 하면 된다
    assert send(client, world["alice"], world, kind="consume", item_id=milk).status_code == 400  # 주인
    assert send(client, world["dave"], world, kind="consume", item_id=milk).status_code == 404  # 볼 수 없는 냉장고는 있는지도 모른다
    client.patch(f"/fridges/{world['fridge_id']}", headers=world["alice"]["headers"], json={"visibility": "public"})
    stranger = send(client, world["dave"], world, kind="consume", item_id=milk)
    assert stranger.status_code == 403 and "친구" in stranger.json()["detail"]  # 공개 냉장고는 보이지만 친구가 아니면 요청은 못 보낸다
    assert client.post("/change-requests", headers=world["bob"]["headers"], json={"fridge_id": 99999, "kind": "add", "name": "x"}).status_code == 404


def test_a_friend_may_ask_about_a_fridge_that_is_visible_to_friends(client, world):
    second = client.post("/fridges", headers=world["alice"]["headers"], json={"name": "김치냉장고"}).json()["id"]
    body = {"fridge_id": second, "kind": "add", "name": "김장 김치"}
    assert client.post("/change-requests", headers=world["bob"]["headers"], json=body).status_code == 404  # 아직 비공개
    client.patch(f"/fridges/{second}", headers=world["alice"]["headers"], json={"visibility": "friends"})
    assert client.post("/change-requests", headers=world["bob"]["headers"], json=body).status_code == 201  # 친구에게 보이는 냉장고
    assert client.post("/change-requests", headers=world["dave"]["headers"], json=body).status_code == 404  # 친구가 아닌 사람에게는 안 보인다
    assert [r["fridge_id"] for r in received(client, world, fridge_id=second)] == [second]
    assert [r["fridge_id"] for r in received(client, world, fridge_id=world["fridge_id"])] == []


def test_unanswered_requests_are_capped_per_fridge(client, world, monkeypatch):
    monkeypatch.setattr(change_requests, "MAX_PENDING_PER_FRIDGE", 2)
    for _ in range(2):
        assert send(client, world["bob"], world, kind="consume", item_id=world["milk"]).status_code == 201
    assert send(client, world["bob"], world, kind="consume", item_id=world["milk"]).status_code == 429


def test_unfriending_cancels_what_is_still_waiting(client, world):
    send(client, world["bob"], world, kind="consume", item_id=world["milk"])
    assert client.delete(f"/friends/{world['bob']['id']}", headers=world["alice"]["headers"]).status_code == 204
    assert received(client, world) == []
    assert [r["status"] for r in client.get("/change-requests/sent", headers=world["bob"]["headers"]).json()] == ["cancelled"]
    assert send(client, world["bob"], world, kind="consume", item_id=world["milk"]).status_code == 404  # 이제 볼 수 없다


def test_the_owner_sees_requests_from_every_fridge_oldest_first(client, world):
    second = client.post("/fridges", headers=world["alice"]["headers"], json={"name": "두 번째"}).json()["id"]
    client.patch(f"/fridges/{second}", headers=world["alice"]["headers"], json={"visibility": "friends"})
    first = send(client, world["bob"], world, kind="add", name="하나").json()["id"]
    later = client.post("/change-requests", headers=world["bob"]["headers"], json={"fridge_id": second, "kind": "add", "name": "둘"}).json()["id"]
    assert [r["id"] for r in received(client, world)] == [first, later]
    assert [(r["fridge_name"], r["item_name"]) for r in received(client, world)][1] == ("두 번째", "둘")
    assert received(client, world, limit=1)[0]["id"] == first
    assert client.get("/change-requests/received", params={"status": "bogus"}, headers=world["alice"]["headers"]).status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [("post", "/change-requests"), ("get", "/change-requests/sent"), ("get", "/change-requests/received"),
     ("delete", "/change-requests/1"), ("post", "/change-requests/1/approve"), ("post", "/change-requests/1/decline")],
)
def test_login_is_required(client, method, path):
    kwargs = {"json": {"fridge_id": 1, "kind": "add", "name": "x"}} if method == "post" and path == "/change-requests" else {}
    assert getattr(client, method)(path, **kwargs).status_code in (401, 403)
