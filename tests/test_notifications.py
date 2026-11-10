"""알림 아이콘: 소비기한 임박 식품, 내 글에 달린 댓글 내용과 새 댓글 수, 읽음 표시."""

from datetime import timedelta

import pytest

from app.timeutil import today
from tests.helpers import auth, default_fridge_id, signup


@pytest.fixture
def world(client):
    """냉장고 주인 alice(두부 있음)와, 아직 alice의 냉장고와 관계가 없는 bob, carol."""
    people = {}
    for username in ("alice", "bob_kim", "carol"):
        tokens = signup(client, username, nickname=username.split("_")[0])
        people[username.split("_")[0]] = {"headers": auth(tokens), "id": tokens["user"]["id"], "tokens": tokens}
    fridge_id = default_fridge_id(client, people["alice"]["tokens"])
    assert client.post(f"/fridges/{fridge_id}/items", headers=people["alice"]["headers"], json={"name": "두부", "quantity": 3}).status_code == 201
    return {**people, "fridge_id": fridge_id}


def summary(client, who):
    response = client.get("/notifications/summary", headers=who["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def write_post(client, who, title="오늘의 냉장고"):
    return client.post("/posts", headers=who["headers"], json={"title": title, "body": "본문"}).json()["id"]


def comment(client, who, post_id, body="좋은 글이에요"):
    response = client.post(f"/posts/{post_id}/comments", headers=who["headers"], json={"body": body})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def comments(client, who, **params):
    response = client.get("/notifications/comments", params=params, headers=who["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def mark_read(client, who, **body):
    return client.post("/notifications/comments/read", headers=who["headers"], json=body)


def test_nothing_new_at_first(client, world):
    assert summary(client, world["alice"]) == {"expiring_items": 0, "unread_comments": 0, "total": 0}  # 변경 요청 숫자는 친구 기능과 함께 없어졌다


# ── 댓글 ─────────────────────────────────────────────────────────────────


def test_comments_on_my_post_are_unread_until_marked_read(client, world):
    post_id = write_post(client, world["alice"], "두부 요리 추천")
    first = comment(client, world["bob"], post_id, "두부조림 해 먹어요")
    second = comment(client, world["carol"], post_id, "저는 부침이요")
    assert summary(client, world["alice"]) == {"expiring_items": 0, "unread_comments": 2, "total": 2}

    feed = comments(client, world["alice"])
    assert [c["comment_id"] for c in feed["comments"]] == [second, first] and feed["unread_count"] == 2  # 최신순
    newest = feed["comments"][0]
    assert (newest["post_id"], newest["post_title"], newest["body"], newest["unread"]) == (post_id, "두부 요리 추천", "저는 부침이요", True)
    assert (newest["commenter"]["id"], newest["commenter"]["nickname"]) == (world["carol"]["id"], "carol")
    assert newest["created_at"].endswith("Z")

    read = mark_read(client, world["alice"])
    assert read.status_code == 200 and read.json() == {"expiring_items": 0, "unread_comments": 0, "total": 0}
    feed = comments(client, world["alice"])
    assert [c["unread"] for c in feed["comments"]] == [False, False] and feed["unread_count"] == 0  # 읽어도 내용은 계속 볼 수 있다
    third = comment(client, world["bob"], post_id, "하나 더요")
    assert summary(client, world["alice"])["unread_comments"] == 1
    assert [(c["comment_id"], c["unread"]) for c in comments(client, world["alice"])["comments"]] == [(third, True), (second, False), (first, False)]


def test_only_other_peoples_comments_on_my_live_posts_count(client, world):
    mine, theirs = write_post(client, world["alice"]), write_post(client, world["bob"], "밥 사진")
    comment(client, world["alice"], mine, "내가 단 내 글의 댓글")  # 내 댓글은 알릴 필요가 없다
    comment(client, world["alice"], theirs, "남의 글에 단 내 댓글")
    comment(client, world["carol"], theirs, "남의 글의 댓글")  # 내 글이 아니다
    assert summary(client, world["alice"])["unread_comments"] == 0
    assert comments(client, world["alice"])["comments"] == []
    assert summary(client, world["bob"])["unread_comments"] == 2  # bob 글에 alice와 carol이 달았다

    deleted = comment(client, world["bob"], mine, "곧 지울 댓글")
    keep = comment(client, world["carol"], mine, "남는 댓글")
    assert summary(client, world["alice"])["unread_comments"] == 2
    client.delete(f"/posts/{mine}/comments/{deleted}", headers=world["bob"]["headers"])
    assert summary(client, world["alice"])["unread_comments"] == 1
    assert [c["comment_id"] for c in comments(client, world["alice"])["comments"]] == [keep]
    client.delete(f"/posts/{mine}", headers=world["alice"]["headers"])  # 글을 지우면 댓글도 안 보인다
    assert summary(client, world["alice"])["unread_comments"] == 0
    assert comments(client, world["alice"])["comments"] == []


def test_read_marks_are_per_person(client, world):
    post_id = write_post(client, world["alice"])
    comment(client, world["carol"], post_id)
    other = write_post(client, world["bob"])
    comment(client, world["carol"], other)
    mark_read(client, world["alice"])
    assert summary(client, world["alice"])["unread_comments"] == 0
    assert summary(client, world["bob"])["unread_comments"] == 1  # alice가 읽어도 bob은 그대로다


def test_mark_read_up_to_an_id_keeps_the_newer_ones_unread(client, world):
    post_id = write_post(client, world["alice"])
    ids = [comment(client, world["bob"], post_id, f"댓글 {n}") for n in range(4)]
    after = mark_read(client, world["alice"], up_to_id=ids[1])
    assert after.json()["unread_comments"] == 2
    assert [(c["comment_id"], c["unread"]) for c in comments(client, world["alice"])["comments"]] == [(ids[3], True), (ids[2], True), (ids[1], False), (ids[0], False)]
    assert mark_read(client, world["alice"], up_to_id=ids[0]).json()["unread_comments"] == 2  # 이미 읽은 자리보다 앞으로는 돌아가지 않는다


def test_mark_read_cannot_skip_comments_that_do_not_exist_yet(client, world):
    post_id = write_post(client, world["alice"])
    comment(client, world["bob"], post_id)
    assert mark_read(client, world["alice"], up_to_id=10**9).json()["unread_comments"] == 0
    comment(client, world["bob"], post_id, "나중에 달린 댓글")
    assert summary(client, world["alice"])["unread_comments"] == 1  # 미리 큰 번호를 보내도 앞으로 달릴 것까지 읽음이 되지 않는다


def test_mark_read_body_is_optional_and_validated(client, world):
    assert client.post("/notifications/comments/read", headers=world["alice"]["headers"]).status_code == 200  # 본문 없이
    assert mark_read(client, world["alice"]).status_code == 200  # 빈 본문
    assert mark_read(client, world["alice"], up_to_id=0).status_code == 422
    assert mark_read(client, world["alice"], up_to_id="abc").status_code == 422


def test_comment_feed_pages(client, world):
    post_id = write_post(client, world["alice"])
    ids = [comment(client, world["bob"], post_id, f"댓글 {n}") for n in range(5)]
    first = comments(client, world["alice"], limit=2)
    assert [c["comment_id"] for c in first["comments"]] == ids[::-1][:2] and first["next_before_id"] == ids[3]
    assert first["unread_count"] == 5  # 쪽과 상관없이 전체 새 댓글 수
    second = comments(client, world["alice"], limit=2, before_id=first["next_before_id"])
    assert [c["comment_id"] for c in second["comments"]] == [ids[2], ids[1]] and second["next_before_id"] == ids[1]
    last = comments(client, world["alice"], limit=2, before_id=second["next_before_id"])
    assert [c["comment_id"] for c in last["comments"]] == [ids[0]] and last["next_before_id"] is None
    assert client.get("/notifications/comments", params={"limit": 0}, headers=world["alice"]["headers"]).status_code == 422


def test_login_is_required(client, world):
    for method, path in (("get", "/notifications/summary"), ("get", "/notifications/comments"), ("post", "/notifications/comments/read")):
        assert getattr(client, method)(path).status_code == 401


# ── 소비기한 임박 ────────────────────────────────────────────────────────


def iso(days):
    return (today() + timedelta(days=days)).isoformat()


def add_item(client, who, fridge_id, name, days=None, **extra):
    body = {"name": name, "quantity": 2, **extra}
    if days is not None:
        body["expiry_date"] = iso(days)
    response = client.post(f"/fridges/{fridge_id}/items", headers=who["headers"], json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def expiring(client, who, **params):
    response = client.get("/notifications/expiring", params=params, headers=who["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def test_items_with_three_days_or_less_left_are_alerts_and_overdue_ones_stay(client, world):
    fridge = world["fridge_id"]
    for name, days in (("지난 우유", -2), ("오늘까지 햄", 0), ("사흘 남은 치즈", 3), ("나흘 남은 요거트", 4), ("기한 모르는 물티슈", None)):
        add_item(client, world["alice"], fridge, name, days)
    gone = add_item(client, world["alice"], fridge, "이미 버린 것", -5)
    client.post(f"/fridges/{fridge}/items/{gone}/discard", headers=world["alice"]["headers"])

    assert summary(client, world["alice"]) == {"expiring_items": 3, "unread_comments": 0, "total": 3}
    feed = expiring(client, world["alice"])
    assert [(i["name"], i["days_left"]) for i in feed["items"]] == [("지난 우유", -2), ("오늘까지 햄", 0), ("사흘 남은 치즈", 3)]  # 빠른 순
    assert feed["count"] == 3 and feed["alert_days"] == 3
    first = feed["items"][0]
    assert (first["fridge_id"], first["fridge_name"], first["storage"], first["remaining_quantity"]) == (fridge, first["fridge_name"], first["storage"], 2.0)
    assert first["expiry_date"] == iso(-2) and first["category_label"] and first["item_id"]


def test_an_item_leaves_the_alerts_when_it_is_eaten_discarded_or_given_a_later_date(client, world):
    fridge = world["fridge_id"]
    eaten, thrown, moved = (add_item(client, world["alice"], fridge, name, 1) for name in ("먹을 것", "버릴 것", "날짜 바꿀 것"))
    assert summary(client, world["alice"])["expiring_items"] == 3
    client.post(f"/fridges/{fridge}/items/{eaten}/consume", headers=world["alice"]["headers"], json={})
    client.post(f"/fridges/{fridge}/items/{thrown}/discard", headers=world["alice"]["headers"])
    assert [i["name"] for i in expiring(client, world["alice"])["items"]] == ["날짜 바꿀 것"]
    client.patch(f"/fridges/{fridge}/items/{moved}", headers=world["alice"]["headers"], json={"expiry_date": iso(10)})
    assert summary(client, world["alice"])["expiring_items"] == 0 and expiring(client, world["alice"])["items"] == []


def test_every_fridge_i_am_a_member_of_counts_but_public_viewers_and_strangers_see_nothing(client, world):
    second = client.post("/fridges", headers=world["alice"]["headers"], json={"name": "김치냉장고"}).json()["id"]
    add_item(client, world["alice"], world["fridge_id"], "본냉장고 우유", 1)
    add_item(client, world["alice"], second, "김치냉장고 김치", 2)
    names = {i["name"]: i["fridge_name"] for i in expiring(client, world["alice"])["items"]}
    assert names["김치냉장고 김치"] == "김치냉장고" and set(names) == {"본냉장고 우유", "김치냉장고 김치"}
    client.patch(f"/fridges/{world['fridge_id']}", headers=world["alice"]["headers"], json={"visibility": "public"})
    for viewer in ("bob", "carol"):  # 공개 냉장고를 구경할 수는 있어도 멤버가 아니면 알림은 없다
        assert summary(client, world[viewer])["expiring_items"] == 0
        assert expiring(client, world[viewer])["items"] == []

    token = client.post(f"/fridges/{world['fridge_id']}/share-link", headers=world["alice"]["headers"]).json()["token"]
    assert client.post(f"/share-links/{token}/join", headers=world["carol"]["headers"]).status_code == 200  # 링크로 들어오면 멤버다
    shared = expiring(client, world["carol"])["items"]
    assert [(i["name"], i["fridge_id"]) for i in shared] == [("본냉장고 우유", world["fridge_id"])]  # 공유받은 냉장고의 것만 (김치냉장고는 아니다)


def test_my_own_other_fridge_items_do_not_leak_to_anyone_else(client, world):
    carol_fridge = default_fridge_id(client, world["carol"]["tokens"])
    add_item(client, world["carol"], carol_fridge, "carol의 우유", 0)
    assert [i["name"] for i in expiring(client, world["carol"])["items"]] == ["carol의 우유"]
    assert expiring(client, world["alice"])["items"] == [] and summary(client, world["alice"])["expiring_items"] == 0


def test_the_expiring_list_can_be_cut_but_still_reports_the_full_count(client, world):
    for days in (-1, 0, 1):
        add_item(client, world["alice"], world["fridge_id"], f"{days}일", days)
    cut = expiring(client, world["alice"], limit=2)
    assert [i["days_left"] for i in cut["items"]] == [-1, 0] and cut["count"] == 3
    for bad in (0, 201):
        assert client.get("/notifications/expiring", params={"limit": bad}, headers=world["alice"]["headers"]).status_code == 422


def test_expiring_items_and_comments_add_up_in_the_total_and_need_a_login(client, world):
    add_item(client, world["alice"], world["fridge_id"], "임박한 것", 1)
    comment(client, world["bob"], write_post(client, world["alice"]), "alice 글에 단 댓글")
    assert summary(client, world["alice"]) == {"expiring_items": 1, "unread_comments": 1, "total": 2}
    assert client.get("/notifications/expiring").status_code == 401
