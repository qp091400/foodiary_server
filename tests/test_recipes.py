import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services.recipes import ingredient_key, ingredient_parts, normalize, rank
from app.timeutil import today
from tests.helpers import auth, default_fridge_id, signup


def recipe(id="1", ingredients="달걀 2개, 양파 1개, 소금 약간"):
    return normalize({"RCP_SEQ": id, "RCP_NM": "테스트 요리", "RCP_PARTS_DTLS": ingredients,
                      "MANUAL01": "1. 손질한다.", "MANUAL03": "3. 익힌다."})


def test_matching_preserves_specific_ingredients_and_not_processed_food():
    assert ingredient_key("유정란 계란 10구") == "달걀"
    assert ingredient_key("보리먹인생삼겹살") == "삼겹살"
    assert ingredient_key("고구마") != ingredient_key("감자")
    assert ingredient_key("양파") != ingredient_key("대파")
    assert ingredient_key("연두부") != ingredient_key("두부")
    assert ingredient_key("저염간장") != ingredient_key("간장")
    assert ingredient_key("마늘소스") != ingredient_key("마늘")
    assert ingredient_key("돼지고기(등심, 50g)") != ingredient_key("삼겹살")
    assert ingredient_key("닭고기(다리, 2개)") != ingredient_key("닭가슴살")
    assert ingredient_key("적양배추") != ingredient_key("양배추")
    assert ingredient_key("열무김치") != ingredient_key("김치")
    assert ingredient_key("두부면") != ingredient_key("두부")
    assert ingredient_key("건표고버섯") != ingredient_key("표고버섯")


def test_serving_header_and_parenthesized_commas():
    parts = ingredient_parts("[1인분]조선부추 50g, 달걀 30g(1/2개, 작은 것)\n고명\n깨 약간")
    assert [p["key"] for p in parts] == ["부추", "달걀", "깨"]
    assert recipe()["steps"] == ["손질한다.", "익힌다."]


def test_ranking_counts_no_assumed_pantry_and_requires_main_match():
    foods = [SimpleNamespace(name="계란 10구"), SimpleNamespace(name="양파")]
    hits = rank([recipe(), recipe("2", "달걀 2개, 양파 1개, 당근 1개, 소금 약간")], foods)
    assert hits[0]["id"] == "1"
    assert hits[0]["missing"] == ["소금 약간"]
    assert rank([recipe()], [SimpleNamespace(name="소금")]) == []
    assert rank([recipe()], []) == []


def test_missing_count_priority_with_random_ties_before_limit(monkeypatch):
    from app.services import recipes
    rows = [recipe("a", "달걀 1개, 양파 1개"), recipe("b", "달걀 2개, 양파 2개"),
            recipe("c", "달걀 1개, 양파 1개, 소금 약간"),
            recipe("d", "달걀 1개, 양파 1개, 당근 1개, 소금 약간")]
    foods = [SimpleNamespace(name="계란")]
    monkeypatch.setattr(recipes.random, "shuffle", lambda values: None)
    assert [r["id"] for r in rank(rows, foods)] == ["a", "b", "c", "d"]
    monkeypatch.setattr(recipes.random, "shuffle", lambda values: values.reverse())
    assert [r["id"] for r in rank(rows, foods)] == ["b", "a", "c", "d"]
    assert [r["id"] for r in rank(rows, foods, limit=1)] == ["b"]
    assert [r["id"] for r in rows] == ["a", "b", "c", "d"]


def test_every_selected_ingredient_is_required_in_the_same_recipe():
    foods = [SimpleNamespace(name="우유1L"), SimpleNamespace(name="닭가슴살 2팩")]
    separate = [recipe("milk", "우유 200ml, 달걀 1개"),
                recipe("chicken", "닭가슴살 100g, 양파 1개")]
    assert rank(separate, foods) == []
    combined = recipe("both", "우유 200ml, 닭가슴살 100g, 소금 약간")
    hits = rank([*separate, combined], foods)
    assert [r["id"] for r in hits] == ["both"]
    assert hits[0]["missing"] == ["소금 약간"]
    assert rank([combined], [*foods, SimpleNamespace(name="김치")]) == []
    assert len(rank([combined], [*foods, SimpleNamespace(name="우유")])) == 1


def test_all_selected_rule_keeps_directional_synonyms():
    foods = [SimpleNamespace(name="계란"), SimpleNamespace(name="삼겹살")]
    assert len(rank([recipe("1", "달걀 1개, 돼지고기 100g")], foods)) == 1
    assert rank([recipe("1", "달걀 1개, 닭고기 100g")], foods) == []


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "recipe_data_dir", str(tmp_path))
    (tmp_path / "catalogue.json").write_text(json.dumps({"recipes": [recipe()], "source_total": 1,
        "sample": True, "imported_at": "2026-10-28T00:00:00Z"}), encoding="utf-8")
    return tmp_path


def test_api_checks_access_and_includes_room_freezer_but_excludes_expired(client, catalog):
    user = signup(client)
    fridge = default_fridge_id(client, user)
    headers = auth(user)
    ids = []
    for name, storage, expiry in [("계란", "freezer", 3), ("양파", "room", 5), ("소금", "fridge", -1)]:
        response = client.post(f"/fridges/{fridge}/items", headers=headers,
            json={"name": name, "storage": storage, "quantity": 1, "expiry_date": str(today() + timedelta(days=expiry))})
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    path = f"/fridges/{fridge}/recipes/recommendations"
    result = client.post(path, headers=headers, json={"item_ids": ids})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["excluded_expired"] == ["소금"]
    assert len(data["recipes"][0]["matched"]) == 2
    assert data["recipes"][0]["missing"] == ["소금 약간"]
    assert client.post(path, headers=headers, json={"item_ids": []}).json()["recipes"] == []
    assert client.post(path, headers=headers, json={"item_ids": [99999]}).status_code == 409
    assert client.post(path, json={"item_ids": ids}).status_code == 401
    other = signup(client, "other")
    assert client.post(path, headers=auth(other), json={"item_ids": ids}).status_code == 404
    assert client.get(f"/fridges/{fridge}/recipes/1", headers=auth(other)).status_code == 404
    assert client.get(f"/fridges/{fridge}/recipes/1", headers=headers).json()["steps"]


def test_failed_full_import_preserves_catalogue(catalog, monkeypatch):
    from scripts import import_recipes
    before = (catalog / "catalogue.json").read_bytes()
    monkeypatch.setattr(settings, "foodsafety_api_key", "test-key")
    monkeypatch.setattr(import_recipes, "fetch", lambda *args: {"total_count": "2", "row": [{"RCP_SEQ": "1"}]})
    with pytest.raises(RuntimeError, match="누락"):
        import_recipes.run(False)
    assert (catalog / "catalogue.json").read_bytes() == before


def test_step_text_is_cleaned_and_step_photos_are_kept_in_order():
    from app.services.recipes import presentable
    row = normalize({"RCP_SEQ": "7", "RCP_NM": "달걀찜", "RCP_PARTS_DTLS": "달걀 2개, 물 100ml",
                     "MANUAL01": "1. 달걀을 푼다.a", "MANUAL_IMG01": "http://www.foodsafetykorea.go.kr/uploadimg/cook/20_00007_1.png",
                     "MANUAL02": "2. 찜기에 10분 찐다.b", "MANUAL03": "",
                     "ATT_FILE_NO_MAIN": "http://www.foodsafetykorea.go.kr/uploadimg/cook/10_00007_2.png"})
    assert row["steps"] == ["달걀을 푼다.", "찜기에 10분 찐다."]
    assert row["step_images"] == ["https://www.foodsafetykorea.go.kr/uploadimg/cook/20_00007_1.png", ""]
    assert row["image"] == "https://www.foodsafetykorea.go.kr/uploadimg/cook/10_00007_2.png"
    # 사진 없이 예전에 들어온 자료도 상세에서 같은 모양이 되고, 끝 글자가 정리된다
    old = {"id": "8", "name": "x", "steps": ["손질한다.c", "익힌다."]}
    assert presentable(old)["steps"] == ["손질한다.", "익힌다."]
    assert presentable(old)["step_images"] == ["", ""] and presentable(old)["image"] == ""
    # 영어 단어로 끝나는 정상 문장은 건드리지 않는다
    assert presentable({"steps": ["소스는 BBQ"]})["steps"] == ["소스는 BBQ"]
    # 원문 문장 중간의 줄바꿈은 이어 붙인다
    assert presentable({"steps": ["매실청 및\n다진 마늘을 넣는다."]})["steps"] == ["매실청 및 다진 마늘을 넣는다."]


def test_fewest_missing_first_then_recipes_with_steps(monkeypatch):
    from app.services import recipes
    link_short = {**recipe("link-short", "달걀 1개, 양파 1개"), "steps": []}
    steps_short = recipe("steps-short", "달걀 2개, 양파 2개")
    steps_long = recipe("steps-long", "달걀 1개, 양파 1개, 당근 1개")
    monkeypatch.setattr(recipes.random, "shuffle", lambda values: None)
    ids = [r["id"] for r in rank([steps_long, link_short, steps_short], [SimpleNamespace(name="계란")])]
    # 부족한 재료가 적은 것이 먼저이고, 같으면 조리 순서가 있는 공공 레시피가 먼저다
    assert ids == ["steps-short", "link-short", "steps-long"]
