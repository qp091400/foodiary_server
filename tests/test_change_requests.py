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


@pytest.mark.parametrize("kind", ["add", "consume", "discard"])
def test_view_only_participants_cannot_send_new_change_requests(client, world, kind):
    body = {"kind": kind, "quantity": 1}
    if kind == "add": body["name"] = "추가 식품"
    else: body["item_id"] = world["milk"]
    assert send(client, world["bob"], world, **body).status_code == 403
    assert received(client, world) == []


def test_editors_change_inventory_directly_without_request_approval(client, world):
    response = client.post(f"/fridges/{world['fridge_id']}/items", headers=world["carol"]["headers"], json={"name": "함께 넣은 식품"})
    assert response.status_code == 201
    assert send(client, world["carol"], world, kind="consume", item_id=world["milk"]).status_code == 400


def test_outsiders_cannot_send_requests_or_change_inventory(client, world):
    assert send(client, world["dave"], world, kind="consume", item_id=world["milk"]).status_code == 404
    assert client.post(f"/fridges/{world['fridge_id']}/items", headers=world["dave"]["headers"], json={"name": "외부"}).status_code == 404
