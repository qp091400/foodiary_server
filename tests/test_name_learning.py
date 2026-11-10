"""상품 이름 보정 두 가지: 사용자가 고친 이름을 다음 영수증에 적용하기(learned_names), 흐리게 읽은 줄은 넓게 고치고 알리기."""

import pytest
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import NameCorrection
from app.routers.receipts import WARN_NAME_UNSURE
from app.services import learned_names
from app.services.expiry import classify
from app.services.name_fix import fix_name, is_recognized
from app.services.ocr import get_ocr_engine
from app.services.receipt_parser import parse_receipt
from tests.helpers import FakeOcrEngine, auth, default_fridge_id, image_bytes, make_boxes, signup

# 이 이름은 규칙 보정(name_fix)이 건드리지 않는 오타라서, 사용자가 고친 기록으로만 바뀐다
MISREAD = "해찬들쌈쟝 500g"
FIXED = "해찬들쌈장 500g"
ROWS = [["이마트 성수점"], ["2026-10-10 14:22"], ["상품명", "단가", "수량", "금액"], [MISREAD, "3,500", "1", "3,500"], ["합계", "3,500"]]


def test_the_test_misread_is_not_fixed_by_the_rules():
    assert fix_name(MISREAD) == MISREAD


# ── 고친 이름에서 오타만 뽑기 ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("read", "final", "expected"),
    [
        ("코카율라 500ml", "코카콜라 500ml", [("코카율라", "코카콜라")]),  # 달라진 낱말만
        ("해찬들쌈쟝 500g", "해찬들쌈장 1kg", [("해찬들쌈쟝", "해찬들쌈장")]),  # 숫자를 고친 것은 따로 치지 않는다
        ("코카율라 제로", "코카콜라제로", [("코카율라 제로", "코카콜라제로")]),  # 낱말 수가 달라지면 이름 전체
        ("삼겹살 구이용", "돼지고기", []),  # 통째로 바꾼 것은 오타가 아니다
        ("우유 1L", "우유 2L", []),  # 한글이 없는 낱말
        ("서울우유 1L", "서울우유 1L", []),
        ("우유", "라면", []),
        ("가", "나", []),  # 한 글자
        ("", "코카콜라", []),
    ],
)
def test_only_typo_like_changes_are_recorded(read, final, expected):
    assert learned_names.correction_pairs(read, final) == expected


def test_a_word_put_back_to_what_was_read_counts_as_rejecting_the_correction():
    assert learned_names.rejected_words("코카율라 500ml", "코카콜라 500ml", "코카율라 500ml") == ["코카율라"]
    assert learned_names.rejected_words("코카율라 500ml", "코카콜라 500ml", "코카콜라 500ml") == []  # 받아들였다
    assert learned_names.rejected_words("코카율라 500ml", "코카콜라 500ml", "펩시 500ml") == []
    assert learned_names.rejected_words("코카율라", "코카콜라", "코카율라 제로") == []  # 낱말 수가 달라지면 알 수 없다


def test_learned_names_fix_words_and_whole_names():
    learned = learned_names.LearnedNames({"해찬들쌈쟝": "해찬들쌈장", "코카율라 제로": "코카콜라제로"})
    assert learned.fix("해찬들쌈쟝 500g") == "해찬들쌈장 500g"
    assert learned.fix("코카율라 제로") == "코카콜라제로"
    assert learned.fix("서울우유 1L") is None
    assert learned_names.LearnedNames().fix("해찬들쌈쟝") is None


# ── 누구에게 적용하나 ────────────────────────────────────────────────────


@pytest.fixture
def people(client):
    def make(name):
        tokens = signup(client, f"user_{name}")
        return {"headers": auth(tokens), "id": default_fridge_id(client, tokens), "user_id": tokens["user"]["id"]}

    return make


def vote(user_id, seen="해찬들쌈쟝", fixed="해찬들쌈장"):
    with SessionLocal() as db:
        db.add(NameCorrection(user_id=user_id, seen=seen, fixed=fixed))
        db.commit()


def fixes_for(user_id, *names):
    with SessionLocal() as db:
        return learned_names.load(db, user_id, names)


def test_the_person_who_corrected_gets_it_at_once_but_others_need_enough_people(client, people):
    a, b, c = people("a"), people("b"), people("c")
    vote(a["user_id"])
    assert fixes_for(a["user_id"], MISREAD).fix(MISREAD) == FIXED
    assert fixes_for(b["user_id"], MISREAD).fix(MISREAD) is None  # 한 사람이 고친 것은 다른 사람에게 퍼지지 않는다
    assert fixes_for(None, MISREAD).fix(MISREAD) is None
    vote(b["user_id"])
    assert fixes_for(c["user_id"], MISREAD).fix(MISREAD) == FIXED  # 두 사람이 같게 고쳤다
    assert fixes_for(None, MISREAD).fix(MISREAD) == FIXED


def test_the_minimum_number_of_people_can_be_changed(client, people, monkeypatch):
    a, b, c = people("a"), people("b"), people("c")
    vote(a["user_id"])
    monkeypatch.setattr(settings, "name_learning_min_users", 1)
    assert fixes_for(c["user_id"], MISREAD).fix(MISREAD) == FIXED
    monkeypatch.setattr(settings, "name_learning_min_users", 3)
    vote(b["user_id"])
    assert fixes_for(c["user_id"], MISREAD).fix(MISREAD) is None


def test_two_equally_strong_corrections_choose_nothing_but_the_stronger_one_wins(client, people):
    a, b, c, d, e = (people(name) for name in "abcde")
    vote(a["user_id"], fixed="해찬들쌈장")
    vote(b["user_id"], fixed="해찬들쌈장")
    vote(c["user_id"], fixed="해찬들쌈쟁")
    vote(d["user_id"], fixed="해찬들쌈쟁")
    assert fixes_for(e["user_id"], "해찬들쌈쟝").fix("해찬들쌈쟝") is None  # 2 대 2
    vote(e["user_id"], fixed="해찬들쌈장")
    other = people("f")
    assert fixes_for(other["user_id"], "해찬들쌈쟝").fix("해찬들쌈쟝") == "해찬들쌈장"  # 3 대 2


def test_my_own_correction_beats_what_other_people_chose(client, people):
    a, b, c = people("a"), people("b"), people("c")
    vote(a["user_id"], fixed="해찬들쌈장")
    vote(b["user_id"], fixed="해찬들쌈장")
    vote(c["user_id"], fixed="해찬들쌈쟁")
    assert fixes_for(c["user_id"], "해찬들쌈쟝").fix("해찬들쌈쟝") == "해찬들쌈쟁"


def test_the_feature_can_be_switched_off(client, people, monkeypatch):
    a = people("a")
    vote(a["user_id"])
    monkeypatch.setattr(settings, "name_learning_enabled", False)
    assert fixes_for(a["user_id"], MISREAD).fix(MISREAD) is None
    with SessionLocal() as db:
        learned_names.learn(db, a["user_id"], [("코카율라", "코카율라", "코카콜라")])
        assert db.scalar(select(NameCorrection).where(NameCorrection.seen == "코카율라")) is None


# ── 기록하기 ─────────────────────────────────────────────────────────────


def test_learn_replaces_my_earlier_correction_of_the_same_name(client, people):
    a = people("a")
    with SessionLocal() as db:
        learned_names.learn(db, a["user_id"], [("해찬들쌈쟝", "해찬들쌈쟝", "해찬들쌈장")])
        learned_names.learn(db, a["user_id"], [("해찬들쌈쟝", "해찬들쌈쟝", "해찬들쌈쟁")])
        rows = db.scalars(select(NameCorrection)).all()
        assert [(row.seen, row.fixed) for row in rows] == [("해찬들쌈쟝", "해찬들쌈쟁")]


def test_rejecting_a_correction_removes_only_my_record(client, people):
    a, b = people("a"), people("b")
    vote(a["user_id"])
    vote(b["user_id"])
    with SessionLocal() as db:
        learned_names.learn(db, a["user_id"], [("해찬들쌈쟝", "해찬들쌈장", "해찬들쌈쟝")])
        assert [row.user_id for row in db.scalars(select(NameCorrection))] == [b["user_id"]]


def test_a_failure_while_recording_never_raises(client, people, monkeypatch):
    a = people("a")
    with SessionLocal() as db:
        monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("DB 오류")))
        learned_names.learn(db, a["user_id"], [("해찬들쌈쟝", "해찬들쌈쟝", "해찬들쌈장")])  # 예외가 나오지 않는다


# ── 영수증 흐름 전체 ─────────────────────────────────────────────────────


def use_ocr(rows, score=0.95):
    engine = FakeOcrEngine(make_boxes(rows, score=score))
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    return engine


def draft_of(client, who, rows=ROWS, score=0.95):
    use_ocr(rows, score)
    response = client.post(f"/fridges/{who['id']}/receipts", headers=who["headers"], files={"file": ("r.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 201, response.text
    return response.json()


def confirm(client, who, draft, name):
    item = {**draft["draft_items"][0], "name": name}
    response = client.post(f"/fridges/{who['id']}/receipts/{draft['id']}/confirm", headers=who["headers"], json={"items": [item]})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_name_the_user_fixed_is_fixed_for_them_on_the_next_receipt(client, people):
    a = people("a")
    first = draft_of(client, a)
    assert first["draft_items"][0]["name"] == MISREAD and first["draft_items"][0]["read_name"] is None
    result = confirm(client, a, first, FIXED)
    assert result["created_items"][0]["name"] == FIXED  # 확정은 사용자가 고친 이름 그대로

    second = draft_of(client, a)
    item = second["draft_items"][0]
    assert (item["name"], item["read_name"]) == (FIXED, MISREAD)
    assert item["category"] == classify(FIXED)


def test_other_people_get_it_after_two_people_fixed_it_and_a_rejection_takes_my_vote_back(client, people):
    a, b, c = people("a"), people("b"), people("c")
    confirm(client, a, draft_of(client, a), FIXED)
    assert draft_of(client, b)["draft_items"][0]["name"] == MISREAD  # 한 사람의 고침은 b에게 적용되지 않는다
    confirm(client, b, draft_of(client, b), FIXED)
    assert draft_of(client, c)["draft_items"][0]["name"] == FIXED  # 두 사람이 같게 고쳤다

    # c가 보정된 이름을 되돌렸다: c에게는 더 이상 적용되지 않는다. a·b는 그대로이므로 다른 사람에게는 여전히 적용된다
    shown = draft_of(client, c)
    assert shown["draft_items"][0]["name"] == FIXED
    confirm(client, c, shown, MISREAD)
    with SessionLocal() as db:
        assert db.scalar(select(NameCorrection).where(NameCorrection.user_id == c["user_id"])) is None
    assert draft_of(client, people("d"))["draft_items"][0]["name"] == FIXED


def test_a_rename_that_is_not_a_typo_is_not_remembered(client, people):
    a = people("a")
    confirm(client, a, draft_of(client, a), "된장")
    with SessionLocal() as db:
        assert db.scalars(select(NameCorrection)).all() == []
    assert draft_of(client, a)["draft_items"][0]["name"] == MISREAD


def test_leaving_the_name_alone_records_nothing(client, people):
    a = people("a")
    confirm(client, a, draft_of(client, a), MISREAD)
    with SessionLocal() as db:
        assert db.scalars(select(NameCorrection)).all() == []


def test_names_that_become_the_same_after_fixing_are_merged(client, people):
    a = people("a")
    vote(a["user_id"])
    rows = [["상품명", "단가", "수량", "금액"], [MISREAD, "3,500", "1", "3,500"], [FIXED, "3,500", "1", "3,500"], ["합계", "7,000"]]
    items = draft_of(client, a, rows)["draft_items"]
    assert [(item["name"], item["quantity"], item["amount"]) for item in items] == [(FIXED, 2.0, 7000)]


def test_excluded_lines_are_not_learned_from(client, people):
    a = people("a")
    draft = draft_of(client, a)
    item = {**draft["draft_items"][0], "name": FIXED, "include": False}
    assert client.post(f"/fridges/{a['id']}/receipts/{draft['id']}/confirm", headers=a["headers"], json={"items": [item]}).status_code == 200
    with SessionLocal() as db:
        assert db.scalars(select(NameCorrection)).all() == []


def test_deleting_a_user_removes_their_corrections(client, people):
    a = people("a")
    vote(a["user_id"])
    with SessionLocal() as db:
        from app.models import User

        db.delete(db.get(User, a["user_id"]))
        db.commit()
        assert db.scalars(select(NameCorrection)).all() == []


# ── 흐리게 읽은 줄 ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "confidence", "expected"),
    [
        ("양숙이 200g", 0.5, "양송이 200g"),  # 한 글자가 통째로 다른 세 글자 낱말: 흐리게 읽었을 때만 고친다
        ("양숙이 200g", 0.95, "양숙이 200g"),
        ("양숙이 200g", None, "양숙이 200g"),
        ("코카율라(L)", 0.95, "코카콜라(L)"),  # 네 글자 이상은 신뢰도와 상관없이 고친다 (예전 그대로)
        ("서울우규 1L", 0.95, "서울우유 1L"),
    ],
)
def test_blurry_lines_are_fixed_more_widely(raw, confidence, expected):
    assert fix_name(raw, confidence) == expected


def test_the_blurry_limit_can_be_changed(monkeypatch):
    monkeypatch.setattr(settings, "name_unsure_below", 0.99)
    assert fix_name("양숙이 200g", 0.95) == "양송이 200g"


@pytest.mark.parametrize(("name", "known"), [("서울우유 1L", True), ("풀무원 두부", True), ("양송이", True), ("해찬들쌈쟝", False), ("123", False)])
def test_is_recognized(name, known):
    assert is_recognized(name) is known


def parsed_items(name, score):
    rows = [["상품명", "단가", "수량", "금액"], [name, "3,500", "1", "3,500"], ["합계", "3,500"]]
    return parse_receipt(make_boxes(rows, score=score)).items


def test_an_unknown_name_on_a_blurry_line_is_flagged_but_known_or_clear_ones_are_not():
    assert [item.name_unsure for item in parsed_items(MISREAD, 0.6)] == [True]
    assert [item.name_unsure for item in parsed_items(MISREAD, 0.95)] == [False]  # 또렷하게 읽었으면 새 상품일 수 있다
    assert [item.name_unsure for item in parsed_items("서울우유 1L", 0.6)] == [False]  # 아는 낱말이 있다
    assert [item.name_unsure for item in parsed_items("양숙이 200g", 0.6)] == [False]  # 넓게 고쳐서 아는 이름이 되었다


def test_a_blurry_name_adds_one_warning_to_the_draft_and_nothing_else(client, people):
    a = people("a")
    draft = draft_of(client, a, score=0.6)
    assert draft["draft_items"][0]["name_unsure"] is True
    assert draft["warnings"] == [WARN_NAME_UNSURE]
    clear = draft_of(client, a, score=0.95)
    assert clear["draft_items"][0]["name_unsure"] is False and clear["warnings"] == []


def test_the_warning_goes_away_once_the_receipt_is_confirmed(client, people):
    a = people("a")
    draft = draft_of(client, a, score=0.6)
    confirm(client, a, draft, FIXED)
    detail = client.get(f"/fridges/{a['id']}/receipts/{draft['id']}", headers=a["headers"]).json()
    assert detail["warnings"] == []


def test_a_corrected_name_is_not_flagged_even_on_a_blurry_line(client, people):
    a = people("a")
    vote(a["user_id"])
    item = draft_of(client, a, score=0.6)["draft_items"][0]
    assert (item["name"], item["name_unsure"]) == (FIXED, False)
