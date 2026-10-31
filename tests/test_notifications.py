"""알림 아이콘: 답하지 않은 변경 요청 수, 내가 보낸 요청의 결과, 내 글에 달린 댓글 내용과 새 댓글 수, 읽음 표시."""

import pytest

from tests.helpers import auth, default_fridge_id, signup


@pytest.fixture
def world(client):
    """냉장고 주인 alice(두부 있음), 보기 친구 bob, 고치기 친구 없이 글만 쓰는 carol."""
    people = {}
    for username in ("alice", "bob_kim", "carol"):
        tokens = signup(client, username, nickname=username.split("_")[0])
        people[username.split("_")[0]] = {"headers": auth(tokens), "id": tokens["user"]["id"], "tokens": tokens}
    alice, bob = people["alice"], people["bob"]
    fridge_id = default_fridge_id(client, alice["tokens"])
    assert client.post(f"/fridges/{fridge_id}/items", headers=alice["headers"], json={"name": "두부", "quantity": 3}).status_code == 201
    token = client.post(f"/fridges/{fridge_id}/share-links", headers=alice["headers"], json={"permission": "viewer"}).json()["token"]
    assert client.post(f"/share-links/{token}/join", headers=bob["headers"]).status_code == 200
    item_id = client.get(f"/fridges/{fridge_id}/items", headers=alice["headers"]).json()[0]["id"]
    return {**people, "fridge_id": fridge_id, "item_id": item_id}


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


def ask(client, world, quantity=1):
    response = client.post(
        "/change-requests",
        headers=world["bob"]["headers"],
        json={"fridge_id": world["fridge_id"], "kind": "consume", "item_id": world["item_id"], "quantity": quantity},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def comments(client, who, **params):
    response = client.get("/notifications/comments", params=params, headers=who["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def mark_read(client, who, **body):
    return client.post("/notifications/comments/read", headers=who["headers"], json=body)


# ── 변경 요청 ────────────────────────────────────────────────────────────


def test_nothing_new_at_first(client, world):
    assert summary(client, world["alice"]) == {"pending_change_requests": 0, "unread_change_results": 0, "unread_comments": 0, "total": 0}


def test_pending_change_requests_show_up_for_the_owner_only(client, world):
    first, second = ask(client, world), ask(client, world)
    assert summary(client, world["alice"]) == {"pending_change_requests": 2, "unread_change_results": 0, "unread_comments": 0, "total": 2}
    assert summary(client, world["bob"])["pending_change_requests"] == 0  # 보낸 사람 쪽에는 뜨지 않는다
    assert summary(client, world["carol"])["total"] == 0
    client.post(f"/change-requests/{first}/approve", headers=world["alice"]["headers"])
    assert summary(client, world["alice"])["pending_change_requests"] == 1
    client.post(f"/change-requests/{second}/decline", headers=world["alice"]["headers"])
    assert summary(client, world["alice"])["pending_change_requests"] == 0


def test_a_cancelled_request_is_not_counted(client, world):
    request_id = ask(client, world)
    client.delete(f"/change-requests/{request_id}", headers=world["bob"]["headers"])
    assert summary(client, world["alice"])["pending_change_requests"] == 0


# ── 댓글 ─────────────────────────────────────────────────────────────────


def test_comments_on_my_post_are_unread_until_marked_read(client, world):
    post_id = write_post(client, world["alice"], "두부 요리 추천")
    first = comment(client, world["bob"], post_id, "두부조림 해 먹어요")
    second = comment(client, world["carol"], post_id, "저는 부침이요")
    assert summary(client, world["alice"]) == {"pending_change_requests": 0, "unread_change_results": 0, "unread_comments": 2, "total": 2}

    feed = comments(client, world["alice"])
    assert [c["comment_id"] for c in feed["comments"]] == [second, first] and feed["unread_count"] == 2  # 최신순
    newest = feed["comments"][0]
    assert (newest["post_id"], newest["post_title"], newest["body"], newest["unread"]) == (post_id, "두부 요리 추천", "저는 부침이요", True)
    assert (newest["commenter"]["id"], newest["commenter"]["nickname"]) == (world["carol"]["id"], "carol")
    assert newest["created_at"].endswith("Z")

    read = mark_read(client, world["alice"])
    assert read.status_code == 200 and read.json() == {"pending_change_requests": 0, "unread_change_results": 0, "unread_comments": 0, "total": 0}
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


def test_total_adds_both_and_login_is_required(client, world):
    ask(client, world)
    comment(client, world["bob"], write_post(client, world["alice"]))
    assert summary(client, world["alice"]) == {"pending_change_requests": 1, "unread_change_results": 0, "unread_comments": 1, "total": 2}
    for method, path in (("get", "/notifications/summary"), ("get", "/notifications/comments"), ("post", "/notifications/comments/read")):
        assert getattr(client, method)(path).status_code == 401


# ── 내가 보낸 변경 요청의 결과 ───────────────────────────────────────────


def results(client, who, **params):
    response = client.get("/notifications/change-results", params=params, headers=who["headers"])
    assert response.status_code == 200, response.text
    return response.json()


def mark_results_read(client, who, **body):
    return client.post("/notifications/change-results/read", headers=who["headers"], json=body)


def handle(client, world, request_id, action):
    response = client.post(f"/change-requests/{request_id}/{action}", headers=world["alice"]["headers"])
    assert response.status_code == 200, response.text


def test_the_sender_is_told_when_a_request_is_approved_or_declined(client, world):
    approved, declined, waiting = ask(client, world), ask(client, world), ask(client, world)
    assert summary(client, world["bob"])["unread_change_results"] == 0  # 답이 오기 전에는 없다
    handle(client, world, approved, "approve")
    handle(client, world, declined, "decline")
    assert summary(client, world["bob"]) == {"pending_change_requests": 0, "unread_change_results": 2, "unread_comments": 0, "total": 2}
    assert summary(client, world["alice"])["unread_change_results"] == 0  # 결과는 보낸 사람에게만 간다
    assert summary(client, world["alice"])["pending_change_requests"] == 1  # 아직 답하지 않은 것 (waiting)

    feed = results(client, world["bob"])
    assert [r["id"] for r in feed["requests"]] == [declined, approved] and feed["unread_count"] == 2  # 최신순, 답이 없는 것은 없다
    assert waiting not in [r["id"] for r in feed["requests"]]
    first = feed["requests"][1]
    assert (first["status"], first["unread"], first["item_name"]) == ("approved", True, "두부")
    assert (first["owner"]["id"], first["owner"]["nickname"]) == (world["alice"]["id"], "alice") and first["handled_at"].endswith("Z")
    assert feed["requests"][0]["status"] == "declined"

    read = mark_results_read(client, world["bob"])
    assert read.status_code == 200 and read.json()["unread_change_results"] == 0 and read.json()["total"] == 0
    feed = results(client, world["bob"])
    assert [r["unread"] for r in feed["requests"]] == [False, False] and feed["unread_count"] == 0  # 확인해도 내용은 계속 볼 수 있다
    handle(client, world, waiting, "approve")
    assert summary(client, world["bob"])["unread_change_results"] == 1  # 새 결과만 다시 뜬다


def test_cancelled_requests_are_not_results(client, world):
    mine = ask(client, world)
    client.delete(f"/change-requests/{mine}", headers=world["bob"]["headers"])  # 내가 취소
    ask(client, world)
    client.delete(f"/friends/{world['bob']['id']}", headers=world["alice"]["headers"])  # 친구가 끊겨 자동 취소
    assert summary(client, world["bob"])["unread_change_results"] == 0
    assert results(client, world["bob"])["requests"] == []


def test_mark_results_read_only_the_given_ids(client, world):
    ids = [ask(client, world) for _ in range(3)]
    for request_id in ids:
        handle(client, world, request_id, "approve")
    assert mark_results_read(client, world["bob"], ids=[ids[0]]).json()["unread_change_results"] == 2
    assert [(r["id"], r["unread"]) for r in results(client, world["bob"])["requests"]] == [(ids[2], True), (ids[1], True), (ids[0], False)]
    assert mark_results_read(client, world["bob"], ids=[]).json()["unread_change_results"] == 2  # 빈 목록은 아무것도 읽지 않는다
    assert mark_results_read(client, world["carol"], ids=ids).json()["unread_change_results"] == 0  # 남의 요청 번호는 영향이 없다
    assert summary(client, world["bob"])["unread_change_results"] == 2
    assert mark_results_read(client, world["bob"], ids=list(range(1, 202))).status_code == 422  # 너무 많다
    assert mark_results_read(client, world["bob"], ids="abc").status_code == 422
    assert client.post("/notifications/change-results/read", headers=world["bob"]["headers"]).json()["unread_change_results"] == 0  # 본문 없이 = 모두


def test_change_result_feed_pages(client, world):
    ids = [ask(client, world) for _ in range(5)]
    for request_id in ids:
        handle(client, world, request_id, "decline")
    first = results(client, world["bob"], limit=2)
    assert [r["id"] for r in first["requests"]] == ids[::-1][:2] and first["next_before_id"] == ids[3] and first["unread_count"] == 5
    second = results(client, world["bob"], limit=2, before_id=first["next_before_id"])
    assert [r["id"] for r in second["requests"]] == [ids[2], ids[1]] and second["next_before_id"] == ids[1]
    last = results(client, world["bob"], limit=2, before_id=second["next_before_id"])
    assert [r["id"] for r in last["requests"]] == [ids[0]] and last["next_before_id"] is None


def test_all_three_kinds_add_up_in_the_total(client, world):
    request_id = ask(client, world)
    handle(client, world, request_id, "approve")
    comment(client, world["alice"], write_post(client, world["bob"]), "bob 글에 단 댓글")
    ask(client, world)  # 이건 alice가 답할 차례
    assert summary(client, world["bob"]) == {"pending_change_requests": 0, "unread_change_results": 1, "unread_comments": 1, "total": 2}
    assert summary(client, world["alice"]) == {"pending_change_requests": 1, "unread_change_results": 0, "unread_comments": 0, "total": 1}
    for method, path in (("get", "/notifications/change-results"), ("post", "/notifications/change-results/read")):
        assert getattr(client, method)(path).status_code == 401
