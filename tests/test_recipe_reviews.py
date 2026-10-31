"""레시피 별점과 후기: 쓰기·고치기·지우기, 별점 요약, 추천·상세에 붙는 별점, 신고."""

import json

import pytest

from app.config import settings
from app.database import SessionLocal
from app.models import RecipeReview
from app.routers import recipe_reviews
from app.services.recipes import normalize
from scripts.set_admin import set_admin
from tests.helpers import auth, default_fridge_id, signup


def recipe(id, name, ingredients="달걀 2개, 양파 1개, 소금 약간"):
    return normalize({"RCP_SEQ": id, "RCP_NM": name, "RCP_PARTS_DTLS": ingredients, "MANUAL01": "1. 손질한다.", "MANUAL02": "2. 익힌다."})


@pytest.fixture(autouse=True)
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "recipe_data_dir", str(tmp_path))
    rows = [recipe("1", "달걀양파볶음"), recipe("2", "달걀양파국")]
    (tmp_path / "catalogue.json").write_text(
        json.dumps({"recipes": rows, "source_total": 2, "sample": True, "imported_at": "2026-10-28T00:00:00Z"}), encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def users(client):
    result = {}
    for name in ("minsu", "jiyeon", "haneul", "boss"):
        tokens = signup(client, name)
        result[name] = {"headers": auth(tokens), "id": tokens["user"]["id"], "tokens": tokens}
    with SessionLocal() as db:
        set_admin(db, "boss@example.com", True)
    return result


def rate(client, who, recipe_id="1", rating=5, **extra):
    return client.put(f"/recipes/{recipe_id}/reviews/mine", headers=who["headers"], json={"rating": rating, **extra})


def listing(client, who, recipe_id="1", **params):
    return client.get(f"/recipes/{recipe_id}/reviews", params=params, headers=who["headers"])


# ── 쓰기와 고치기 ────────────────────────────────────────────────────────


def test_write_a_review_then_edit_it(client, users):
    created = rate(client, users["minsu"], rating=4, body="  간이 딱 맞아요  ")
    assert created.status_code == 201
    body = created.json()
    assert (body["rating"], body["body"], body["recipe_id"], body["recipe_name"]) == (4, "간이 딱 맞아요", "1", "달걀양파볶음")
    assert (body["mine"], body["can_delete"], body["updated_at"]) == (True, True, None)
    assert body["author"]["id"] == users["minsu"]["id"]

    edited = rate(client, users["minsu"], rating=2, body="다시 먹어 보니 짰어요")
    assert edited.status_code == 200
    assert (edited.json()["id"], edited.json()["rating"], edited.json()["body"]) == (body["id"], 2, "다시 먹어 보니 짰어요")
    assert edited.json()["updated_at"] is not None
    assert len(listing(client, users["minsu"]).json()["reviews"]) == 1  # 고쳐도 하나다


def test_saving_the_same_review_again_is_not_an_edit(client, users):
    rate(client, users["minsu"], rating=3, body="무난해요")
    again = rate(client, users["minsu"], rating=3, body="무난해요")
    assert again.status_code == 200 and again.json()["updated_at"] is None


def test_a_rating_alone_is_a_review(client, users):
    for extra in ({}, {"body": None}, {"body": ""}, {"body": "   "}):
        response = rate(client, users["minsu"], rating=5, **extra)
        assert response.status_code in (200, 201) and response.json()["body"] is None
    assert rate(client, users["minsu"], rating=4, body="맛있어요").json()["body"] == "맛있어요"
    assert rate(client, users["minsu"], rating=4, body="").json()["body"] is None  # 글을 비우면 별점만 남는다


def test_review_rules(client, users):
    for rating in (0, 6, -1):
        assert rate(client, users["minsu"], rating=rating).status_code == 422
    assert client.put("/recipes/1/reviews/mine", headers=users["minsu"]["headers"], json={"rating": "good"}).status_code == 422
    assert client.put("/recipes/1/reviews/mine", headers=users["minsu"]["headers"], json={}).status_code == 422
    assert rate(client, users["minsu"], body="가" * 501).status_code == 422
    assert rate(client, users["minsu"], body="가" * 500).status_code == 201
    assert rate(client, users["jiyeon"], "no-such-recipe").status_code == 404
    assert client.put("/recipes/1/reviews/mine", json={"rating": 5}).status_code == 401
    assert client.get("/recipes/1/reviews").status_code == 401


def test_review_flood_is_limited_and_deleted_ones_count(client, users, monkeypatch):
    monkeypatch.setattr(recipe_reviews, "REVIEWS_PER_HOUR", 2)
    first = rate(client, users["minsu"], "1").json()["id"]
    assert rate(client, users["minsu"], "2").status_code == 201
    assert rate(client, users["minsu"], "1", rating=1).status_code == 200  # 고치는 것은 세지 않는다
    client.delete(f"/recipes/1/reviews/{first}", headers=users["minsu"]["headers"])
    limited = rate(client, users["minsu"], "1")  # 지우고 다시 쓰려 해도 센다
    assert limited.status_code == 429 and limited.headers["Retry-After"]
    assert rate(client, users["jiyeon"], "1").status_code == 201  # 다른 사람은 상관없다


# ── 요약과 목록 ──────────────────────────────────────────────────────────


def test_summary_average_and_distribution(client, users):
    empty = listing(client, users["minsu"]).json()
    assert empty["summary"] == {"average": None, "count": 0, "distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0}}
    assert empty["my_review"] is None and empty["reviews"] == [] and empty["next_before_id"] is None
    for name, stars in (("minsu", 5), ("jiyeon", 4), ("haneul", 4)):
        rate(client, users[name], rating=stars)
    rate(client, users["boss"], "2", rating=1)  # 다른 레시피는 섞이지 않는다
    summary = listing(client, users["minsu"]).json()["summary"]
    assert summary == {"average": 4.3, "count": 3, "distribution": {"1": 0, "2": 0, "3": 0, "4": 2, "5": 1}}
    assert listing(client, users["minsu"], "2").json()["summary"]["average"] == 1.0


def test_list_is_newest_first_pages_and_knows_my_review(client, users):
    ids = [rate(client, users[name], body=f"{name}의 후기").json()["id"] for name in ("minsu", "jiyeon", "haneul", "boss")]
    first = listing(client, users["jiyeon"], limit=3).json()
    assert [r["id"] for r in first["reviews"]] == ids[::-1][:3] and first["next_before_id"] == ids[1]
    assert first["my_review"]["id"] == ids[1]
    assert [(r["mine"], r["can_delete"]) for r in first["reviews"]] == [(False, False), (False, False), (True, True)]
    second = listing(client, users["jiyeon"], limit=3, before_id=first["next_before_id"]).json()
    assert [r["id"] for r in second["reviews"]] == [ids[0]] and second["next_before_id"] is None
    assert second["summary"]["count"] == 4  # 쪽과 상관없이 전체 요약
    assert listing(client, users["boss"]).json()["reviews"][0]["can_delete"] is True  # 관리자는 모두 지울 수 있다


def test_my_reviews_across_recipes(client, users):
    one, two = rate(client, users["minsu"], "1", body="첫째").json()["id"], rate(client, users["minsu"], "2", rating=3).json()["id"]
    rate(client, users["jiyeon"], "1")
    mine = client.get("/recipes/reviews/mine", headers=users["minsu"]["headers"]).json()
    assert [(r["id"], r["recipe_id"], r["recipe_name"]) for r in mine["reviews"]] == [(two, "2", "달걀양파국"), (one, "1", "달걀양파볶음")]
    assert mine["next_before_id"] is None
    page = client.get("/recipes/reviews/mine", params={"limit": 1}, headers=users["minsu"]["headers"]).json()
    assert [r["id"] for r in page["reviews"]] == [two] and page["next_before_id"] == two
    rest = client.get("/recipes/reviews/mine", params={"limit": 1, "before_id": two}, headers=users["minsu"]["headers"]).json()
    assert [r["id"] for r in rest["reviews"]] == [one] and rest["next_before_id"] is None


def test_review_keeps_its_recipe_name_after_the_recipe_leaves_the_catalogue(client, users, catalog):
    rate(client, users["minsu"])
    (catalog / "catalogue.json").write_text(json.dumps({"recipes": [], "source_total": 0, "sample": True, "imported_at": None}), encoding="utf-8")
    listed = listing(client, users["jiyeon"]).json()  # 목록은 레시피 자료와 상관없이 보인다
    assert listed["summary"]["count"] == 1 and listed["reviews"][0]["recipe_name"] == "달걀양파볶음"
    assert rate(client, users["minsu"], rating=1).status_code == 404  # 없는 레시피에는 새로 쓰거나 고칠 수 없다


# ── 지우기 ───────────────────────────────────────────────────────────────


def test_only_the_author_or_an_admin_deletes_a_review(client, users):
    review_id = rate(client, users["minsu"], body="후기").json()["id"]
    url = f"/recipes/1/reviews/{review_id}"
    assert client.delete(url, headers=users["jiyeon"]["headers"]).status_code == 403
    assert client.delete(f"/recipes/2/reviews/{review_id}", headers=users["minsu"]["headers"]).status_code == 404  # 다른 레시피 주소로는 못 지운다
    assert client.delete(url, headers=users["minsu"]["headers"]).status_code == 204
    assert client.delete(url, headers=users["minsu"]["headers"]).status_code == 404  # 이미 지웠다
    assert listing(client, users["minsu"]).json()["summary"]["count"] == 0
    other = rate(client, users["jiyeon"]).json()["id"]
    assert client.delete(f"/recipes/1/reviews/{other}", headers=users["boss"]["headers"]).status_code == 204  # 관리자
    with SessionLocal() as db:
        deleted = db.get(RecipeReview, other)
        assert deleted.deleted_at is not None and deleted.deleted_by == users["boss"]["id"]  # 누가 지웠는지 남는다


def test_after_deleting_you_can_write_a_new_one(client, users):
    first = rate(client, users["minsu"], rating=1, body="별로").json()["id"]
    client.delete(f"/recipes/1/reviews/{first}", headers=users["minsu"]["headers"])
    assert listing(client, users["minsu"]).json()["my_review"] is None
    second = rate(client, users["minsu"], rating=5, body="다시 해 보니 맛있어요")
    assert second.status_code == 201 and second.json()["id"] != first
    assert listing(client, users["minsu"]).json()["summary"] == {"average": 5.0, "count": 1, "distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 1}}


def test_a_person_has_one_live_review_per_recipe_in_the_database(client, users):
    from sqlalchemy.exc import IntegrityError

    rate(client, users["minsu"])
    with SessionLocal() as db:
        db.add(RecipeReview(recipe_id="1", recipe_name="달걀양파볶음", author_id=users["minsu"]["id"], rating=3))
        with pytest.raises(IntegrityError):
            db.commit()


# ── 추천과 상세에 붙는 별점 ──────────────────────────────────────────────


def test_recommendations_and_detail_carry_the_rating(client, users):
    rate(client, users["jiyeon"], "1", rating=5)
    rate(client, users["haneul"], "1", rating=4)
    headers = users["minsu"]["headers"]
    fridge = default_fridge_id(client, users["minsu"]["tokens"])
    ids = [
        client.post(f"/fridges/{fridge}/items", headers=headers, json={"name": name, "quantity": 1}).json()["id"] for name in ("계란", "양파")
    ]
    cards = {r["id"]: r for r in client.post(f"/fridges/{fridge}/recipes/recommendations", headers=headers, json={"item_ids": ids}).json()["recipes"]}
    assert (cards["1"]["rating_average"], cards["1"]["rating_count"]) == (4.5, 2)
    assert (cards["2"]["rating_average"], cards["2"]["rating_count"]) == (None, 0)  # 후기가 없으면 평균은 비어 있다

    detail = client.get(f"/fridges/{fridge}/recipes/1", headers=headers).json()
    assert detail["steps"] and detail["rating"]["average"] == 4.5 and detail["rating"]["distribution"]["5"] == 1
    assert detail["my_review_id"] is None
    mine = rate(client, users["minsu"], "1", rating=3).json()["id"]
    detail = client.get(f"/fridges/{fridge}/recipes/1", headers=headers).json()
    assert detail["my_review_id"] == mine and detail["rating"]["count"] == 3


# ── 신고와 관리자 처리 ───────────────────────────────────────────────────


def test_report_a_review_and_the_admin_deletes_it(client, users):
    review_id = rate(client, users["minsu"], rating=1, body="광고 문구").json()["id"]
    url = f"/recipes/1/reviews/{review_id}/report"
    assert client.post(url, headers=users["minsu"]["headers"], json={"reason": "spam"}).status_code == 400  # 내 후기
    assert client.post(url, headers=users["jiyeon"]["headers"], json={"reason": "boring"}).status_code == 422
    assert client.post("/recipes/1/reviews/99999/report", headers=users["jiyeon"]["headers"], json={"reason": "spam"}).status_code == 404
    assert client.post(url, headers=users["jiyeon"]["headers"], json={"reason": "spam", "detail": "광고예요"}).status_code == 201
    assert client.post(url, headers=users["jiyeon"]["headers"], json={"reason": "abuse"}).status_code == 409  # 같은 사람이 또
    assert client.post(url, headers=users["haneul"]["headers"], json={"reason": "spam"}).status_code == 201

    reports = client.get("/admin/reports", headers=users["boss"]["headers"]).json()
    assert [r["target_type"] for r in reports] == ["review", "review"] and reports[0]["open_reports_on_target"] == 2
    target = reports[0]["target"]
    assert (target["recipe_id"], target["recipe_name"], target["post_id"], target["deleted"]) == ("1", "달걀양파볶음", None, False)
    assert "광고 문구" in target["text"] and "별점 1" in target["text"]

    resolved = client.post(f"/admin/reports/{reports[0]['id']}/resolve", headers=users["boss"]["headers"], json={"action": "delete"})
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved" and resolved.json()["target"]["deleted"] is True
    assert listing(client, users["jiyeon"]).json()["summary"]["count"] == 0  # 평균에서도 빠진다
    assert client.get("/admin/reports", headers=users["boss"]["headers"]).json() == []  # 같은 대상의 다른 신고도 닫힌다


def test_admin_can_dismiss_a_review_report(client, users):
    review_id = rate(client, users["minsu"]).json()["id"]
    client.post(f"/recipes/1/reviews/{review_id}/report", headers=users["jiyeon"]["headers"], json={"reason": "other"})
    report = client.get("/admin/reports", headers=users["boss"]["headers"]).json()[0]
    assert client.post(f"/admin/reports/{report['id']}/resolve", headers=users["boss"]["headers"], json={"action": "dismiss"}).json()["status"] == "dismissed"
    assert listing(client, users["jiyeon"]).json()["summary"]["count"] == 1  # 문제없음이면 그대로 남는다
