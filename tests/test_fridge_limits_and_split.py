"""냉장고는 한 사람이 10개까지, 영수증 품목은 내가 고칠 수 있는 냉장고들에 나눠 넣을 수 있다."""

import pytest

from app.main import app
from app.routers import fridges
from app.services.ocr import get_ocr_engine
from tests.helpers import FakeOcrEngine, auth, default_fridge_id, image_bytes, make_boxes, signup

ROWS = [
    ["이마트 성수점"],
    ["2026-10-10 14:22"],
    ["상품명", "단가", "수량", "금액"],
    ["서울우유 1L", "2,980", "1", "2,980"],
    ["풀무원 두부", "1,500", "2", "3,000"],
    ["물티슈 100매", "1,000", "1", "1,000"],
    ["합계", "6,980"],
]


@pytest.fixture
def me(client):
    tokens = signup(client, "minsu")
    return {"headers": auth(tokens), "id": tokens["user"]["id"], "fridge_id": default_fridge_id(client, tokens), "tokens": tokens}


def new_fridge(client, who, name):
    response = client.post("/fridges", headers=who["headers"], json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def draft_of(client, who):
    engine = FakeOcrEngine(make_boxes(ROWS))
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    response = client.post(
        f"/fridges/{who['fridge_id']}/receipts",
        headers=who["headers"],
        files={"file": ("receipt.jpg", image_bytes(), "image/jpeg")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def confirm(client, who, draft, **targets):
    """targets: 품목 이름 → 넣을 냉장고 번호"""
    items = [{**item, **({"fridge_id": targets[item["name"]]} if item["name"] in targets else {})} for item in draft["draft_items"]]
    return client.post(f"/fridges/{who['fridge_id']}/receipts/{draft['id']}/confirm", headers=who["headers"], json={"items": items})


def names_in(client, who, fridge_id):
    return sorted(i["name"] for i in client.get(f"/fridges/{fridge_id}/items", headers=who["headers"]).json())


# ── 냉장고 수 제한 ───────────────────────────────────────────────────────


def test_a_person_owns_at_most_ten_fridges(client, me):
    for number in range(2, 11):  # 가입할 때 하나가 이미 있다
        new_fridge(client, me, f"냉장고 {number}")
    refused = client.post("/fridges", headers=me["headers"], json={"name": "열한 번째"})
    assert refused.status_code == 409 and str(fridges.MAX_FRIDGES_PER_USER) in refused.json()["detail"]
    listed = client.get("/fridges", headers=me["headers"]).json()
    assert len(listed) == 10
    assert client.delete(f"/fridges/{listed[-1]['id']}", headers=me["headers"]).status_code == 204  # 지우면 다시 만들 수 있다
    assert client.post("/fridges", headers=me["headers"], json={"name": "새 냉장고"}).status_code == 201


def test_the_limit_counts_only_fridges_you_own(client, me):
    other = signup(client, "jiyeon")
    other_headers = auth(other)
    for number in range(9):
        new_fridge(client, {"headers": other_headers}, f"지연 {number}")
    token = client.post(f"/fridges/{default_fridge_id(client, other)}/share-links", headers=other_headers, json={"permission": "editor"}).json()["token"]
    assert client.post(f"/share-links/{token}/join", headers=me["headers"]).status_code == 200  # 남의 냉장고에 고치기로 들어가도
    for number in range(9):
        new_fridge(client, me, f"내 냉장고 {number}")  # 내 몫 10개를 다 만들 수 있다
    assert client.post("/fridges", headers=me["headers"], json={"name": "초과"}).status_code == 409
    assert client.post("/fridges", headers=other_headers, json={"name": "초과"}).status_code == 409  # 사람마다 따로 센다


# ── 영수증 품목 나눠 넣기 ────────────────────────────────────────────────


def test_items_go_to_the_fridges_you_pick_and_the_rest_stay_where_the_photo_was_uploaded(client, me):
    second = new_fridge(client, me, "김치냉장고")
    draft = draft_of(client, me)
    response = confirm(client, me, draft, **{"서울우유 1L": second})
    assert response.status_code == 200, response.text
    assert sorted((i["name"], i["fridge_id"]) for i in response.json()["created_items"]) == [("서울우유 1L", second), ("풀무원 두부", me["fridge_id"])]
    assert names_in(client, me, me["fridge_id"]) == ["풀무원 두부"]
    assert names_in(client, me, second) == ["서울우유 1L"]
    # 영수증은 올린 냉장고에 남고, 품목의 구매 기록과 지출은 들어간 냉장고를 따라간다
    assert client.get(f"/fridges/{me['fridge_id']}/receipts/{draft['id']}", headers=me["headers"]).json()["status"] == "confirmed"
    assert client.get(f"/fridges/{second}/receipts/{draft['id']}", headers=me["headers"]).status_code == 404
    assert [p["name"] for p in client.get(f"/fridges/{second}/purchases", params={"start": "2026-10-01", "end": "2026-10-31"}, headers=me["headers"]).json()] == ["서울우유 1L"]
    month = {"year": 2026, "month": 10}
    first_stats = client.get(f"/fridges/{me['fridge_id']}/stats/monthly", params=month, headers=me["headers"]).json()
    second_stats = client.get(f"/fridges/{second}/stats/monthly", params=month, headers=me["headers"]).json()
    assert (first_stats["food_spent"], first_stats["item_count"], first_stats["receipt_count"], first_stats["non_food_spent"]) == (3000, 1, 1, 1000)
    assert (second_stats["food_spent"], second_stats["item_count"], second_stats["receipt_count"], second_stats["non_food_spent"]) == (2980, 1, 0, 0)


def test_naming_the_upload_fridge_itself_is_fine(client, me):
    draft = draft_of(client, me)
    assert confirm(client, me, draft, **{"서울우유 1L": me["fridge_id"]}).status_code == 200
    assert names_in(client, me, me["fridge_id"]) == ["서울우유 1L", "풀무원 두부"]


def test_an_editable_friend_fridge_can_be_a_target(client, me):
    friend = signup(client, "jiyeon")
    friend_fridge = default_fridge_id(client, friend)
    token = client.post(f"/fridges/{friend_fridge}/share-links", headers=auth(friend), json={"permission": "editor"}).json()["token"]
    client.post(f"/share-links/{token}/join", headers=me["headers"])
    draft = draft_of(client, me)
    assert confirm(client, me, draft, **{"풀무원 두부": friend_fridge}).status_code == 200
    seen = client.get(f"/fridges/{friend_fridge}/items", headers=auth(friend)).json()
    assert [(i["name"], i["added_by"]) for i in seen] == [("풀무원 두부", me["id"])]  # 친구 냉장고에 들어가고, 넣은 사람은 나로 남는다


def test_a_view_only_fridge_refuses_and_nothing_is_created(client, me):
    friend = signup(client, "jiyeon")
    friend_fridge = default_fridge_id(client, friend)
    token = client.post(f"/fridges/{friend_fridge}/share-links", headers=auth(friend), json={"permission": "viewer"}).json()["token"]
    client.post(f"/share-links/{token}/join", headers=me["headers"])
    draft = draft_of(client, me)
    refused = confirm(client, me, draft, **{"서울우유 1L": me["fridge_id"], "풀무원 두부": friend_fridge})
    assert refused.status_code == 403
    assert names_in(client, me, me["fridge_id"]) == [] and names_in(client, {"headers": auth(friend)}, friend_fridge) == []
    assert client.get(f"/fridges/{me['fridge_id']}/receipts/{draft['id']}", headers=me["headers"]).json()["status"] == "pending"  # 다시 확정할 수 있다
    assert confirm(client, me, draft, **{"풀무원 두부": me["fridge_id"]}).status_code == 200


def test_unknown_and_unrelated_fridges_are_not_found(client, me):
    stranger = signup(client, "haneul")
    stranger_fridge = default_fridge_id(client, stranger)
    draft = draft_of(client, me)
    assert confirm(client, me, draft, **{"서울우유 1L": 99999}).status_code == 404
    assert confirm(client, me, draft, **{"서울우유 1L": stranger_fridge}).status_code == 404  # 권한이 없는 냉장고는 있는지도 모른다
    assert names_in(client, me, me["fridge_id"]) == []


def test_a_fridge_on_a_line_that_is_not_put_in_a_fridge_is_ignored(client, me):
    draft = draft_of(client, me)
    items = [{**item, "fridge_id": 99999} if not item["include"] else item for item in draft["draft_items"]]
    response = client.post(f"/fridges/{me['fridge_id']}/receipts/{draft['id']}/confirm", headers=me["headers"], json={"items": items})
    assert response.status_code == 200 and response.json()["skipped"] == 1
