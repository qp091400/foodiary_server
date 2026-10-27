from datetime import timedelta
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import FoodItem, Receipt, ReceiptItem
from app.services import barcode as barcode_service
from app.services.foodsafety import BarcodeLookup, ProductInfo
from app.routers.receipts import FULL_PASS_WAIT_SECONDS
from app.services.ocr import get_ocr_engine
from app.services.ocr.base import OcrBusyError
from app.timeutil import today
from tests.helpers import FakeOcrEngine, TwoModelOcrEngine, auth, default_fridge_id, image_bytes, make_boxes, signup

RECEIPT_ROWS = [
    ["이마트 성수점"],
    ["2026-10-10 14:22"],
    ["상품명", "단가", "수량", "금액"],
    ["서울우유 1L", "2,980", "1", "2,980"],
    ["행사할인", "-500"],
    ["풀무원 두부", "1,500", "2", "3,000"],
    ["물티슈 100매", "1,000", "1", "1,000"],
    ["종량제봉투 20L", "490", "1", "490"],
    ["합계", "6,970"],
]


@pytest.fixture
def fridge(client):
    tokens = signup(client, "minsu")
    return {"headers": auth(tokens), "id": default_fridge_id(client, tokens), "user_id": tokens["user"]["id"]}


def use_ocr(rows: list[list[str]]) -> FakeOcrEngine:
    engine = FakeOcrEngine(make_boxes(rows))
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    return engine


def upload(client, fridge, *, name="receipt.jpg", data=None):
    return client.post(
        f"/fridges/{fridge['id']}/receipts",
        headers=fridge["headers"],
        files={"file": (name, data if data is not None else image_bytes(), "image/jpeg")},
    )


def uploaded_files() -> list[Path]:
    return [path for path in Path(settings.upload_dir).rglob("*") if path.is_file()]


def test_upload_returns_draft_without_touching_fridge(client, fridge):
    engine = use_ocr(RECEIPT_ROWS)
    response = upload(client, fridge)
    assert response.status_code == 201, response.text
    draft = response.json()

    assert draft["status"] == "pending"
    assert draft["store_name"] == "이마트 성수점"
    assert draft["purchased_on"] == "2026-10-10"
    assert draft["total_amount"] == 6970
    assert draft["items_sum"] == 6970
    assert draft["warnings"] == []
    assert draft["items"] == []
    assert "서울우유 1L 2,980 1 2,980" in draft["raw_lines"]

    items = draft["draft_items"]
    assert [item["name"] for item in items] == ["서울우유 1L", "풀무원 두부", "물티슈 100매", "종량제봉투 20L"]
    assert [item["line_no"] for item in items] == [1, 2, 3, 4]
    milk, tofu, wipes, bag = items
    assert (milk["amount"], milk["discount"], milk["quantity"], milk["unit_price"]) == (2980, 500, 1.0, 2980)
    assert (milk["category"], milk["category_label"], milk["storage"]) == ("milk", "우유", "fridge")
    assert (milk["is_food"], milk["include"]) == (True, True)
    assert (milk["expiry_date"], milk["expiry_source"]) == ("2026-10-20", "category")  # 구매일 + 10일
    assert milk["confidence"] == pytest.approx(0.95)
    # 상품명에서 뽑은 용량
    assert (milk["size_text"], milk["size_amount"], milk["size_unit"]) == ("1L", 1000, "ml")
    assert (tofu["size_text"], tofu["size_amount"], tofu["size_unit"]) == (None, None, None)
    assert (wipes["size_text"], wipes["size_amount"], wipes["size_unit"]) == ("100매", 100, "매")
    assert (bag["size_text"], bag["size_amount"], bag["size_unit"]) == ("20L", 20000, "ml")
    assert (tofu["category"], tofu["quantity"], tofu["expiry_date"]) == ("tofu", 2.0, "2026-10-17")
    for non_food in (wipes, bag):
        assert non_food["category"] == "non_food"
        assert (non_food["is_food"], non_food["include"]) == (False, False)
        assert non_food["expiry_date"] is None and non_food["expiry_source"] is None

    # 이미지는 uploads/{fridge_id}/{uuid}.jpg로 저장되고, OCR은 그 파일로 돈다
    (saved,) = uploaded_files()
    assert saved.parent.name == str(fridge["id"]) and saved.suffix == ".jpg"
    assert engine.calls == [str(saved)]

    # 확정 전에는 냉장고에 아무것도 없다
    assert client.get(f"/fridges/{fridge['id']}/items", headers=fridge["headers"]).json() == []
    listing = client.get(f"/fridges/{fridge['id']}/receipts", headers=fridge["headers"]).json()
    assert [(row["id"], row["status"], row["item_count"]) for row in listing] == [(draft["id"], "pending", 4)]
    detail = client.get(f"/fridges/{fridge['id']}/receipts/{draft['id']}", headers=fridge["headers"])
    assert detail.status_code == 200 and detail.json() == draft


def test_confirm_with_edits_creates_food_items(client, fridge):
    use_ocr(RECEIPT_ROWS)
    draft = upload(client, fridge).json()
    milk, tofu, wipes, bag = draft["draft_items"]

    # 사용자가 고친 내용: 두부 이름·소비기한 수정, 영수증에 없던 사과 추가, 봉투는 그대로 제외
    tofu = {**tofu, "name": "풀무원 부침두부", "expiry_date": "2026-10-25"}
    apple = {"name": "사과", "quantity": 3, "amount": 6000, "discount": 0, "include": True}
    response = client.post(
        f"/fridges/{fridge['id']}/receipts/{draft['id']}/confirm",
        headers=fridge["headers"],
        json={"store_name": "이마트 성수점", "items": [milk, tofu, wipes, bag, apple]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["receipt_id"] == draft["id"]
    assert result["skipped"] == 2
    assert [item["name"] for item in result["created_items"]] == ["서울우유 1L", "풀무원 부침두부", "사과"]

    foods = {item["name"]: item for item in client.get(f"/fridges/{fridge['id']}/items", headers=fridge["headers"]).json()}
    assert set(foods) == {"서울우유 1L", "풀무원 부침두부", "사과"}  # 비식품은 냉장고에 없다

    milk_item = foods["서울우유 1L"]
    assert milk_item["total_price"] == 2480  # 금액 - 할인
    assert milk_item["unit_price"] == 2980
    assert milk_item["purchased_on"] == "2026-10-10"  # 영수증에서 읽은 날짜
    assert (milk_item["expiry_date"], milk_item["expiry_source"]) == ("2026-10-20", "category")
    assert milk_item["receipt_id"] == draft["id"]
    assert (milk_item["quantity"], milk_item["remaining_quantity"], milk_item["status"]) == (1.0, 1.0, "active")
    assert (milk_item["size_text"], milk_item["size_amount"], milk_item["size_unit"]) == ("1L", 1000, "ml")

    tofu_item = foods["풀무원 부침두부"]
    assert (tofu_item["expiry_date"], tofu_item["expiry_source"]) == ("2026-10-25", "manual")
    assert (tofu_item["quantity"], tofu_item["total_price"]) == (2.0, 3000)

    apple_item = foods["사과"]  # 직접 추가한 줄: 카테고리·소비기한 자동 추정, 단가 = 실결제액 / 수량
    assert (apple_item["category"], apple_item["unit_price"], apple_item["total_price"]) == ("fruit", 2000, 6000)
    assert (apple_item["expiry_date"], apple_item["expiry_source"]) == ("2026-10-17", "category")

    # 모든 줄이 receipt_items에 남는다 (비식품 포함 = 구매 기록)
    with SessionLocal() as db:
        rows = db.scalars(select(ReceiptItem).order_by(ReceiptItem.line_no)).all()
        assert [(row.line_no, row.name, row.included) for row in rows] == [
            (1, "서울우유 1L", True),
            (2, "풀무원 부침두부", True),
            (3, "물티슈 100매", False),
            (4, "종량제봉투 20L", False),
            (5, "사과", True),
        ]
        assert [row.food_item_id is not None for row in rows] == [True, True, False, False, True]
        assert rows[2].category == "non_food"
        assert len(db.scalars(select(FoodItem)).all()) == 3

    detail = client.get(f"/fridges/{fridge['id']}/receipts/{draft['id']}", headers=fridge["headers"]).json()
    assert detail["status"] == "confirmed"
    assert detail["confirmed_at"].endswith("Z")
    assert len(detail["items"]) == 5
    assert detail["items_sum"] == 2480 + 3000 + 1000 + 490 + 6000
    listing = client.get(
        f"/fridges/{fridge['id']}/receipts", headers=fridge["headers"], params={"status": "confirmed"}
    ).json()
    assert [(row["id"], row["item_count"]) for row in listing] == [(draft["id"], 5)]


def test_confirm_twice_409(client, fridge):
    use_ocr(RECEIPT_ROWS)
    draft = upload(client, fridge).json()
    url = f"/fridges/{fridge['id']}/receipts/{draft['id']}/confirm"
    body = {"items": draft["draft_items"]}
    assert client.post(url, headers=fridge["headers"], json=body).status_code == 200
    again = client.post(url, headers=fridge["headers"], json=body)
    assert again.status_code == 409
    assert "이미 확정" in again.json()["detail"]
    assert len(client.get(f"/fridges/{fridge['id']}/items", headers=fridge["headers"]).json()) == 2
    # 확정된 영수증은 지울 수 없다
    assert client.delete(f"/fridges/{fridge['id']}/receipts/{draft['id']}", headers=fridge["headers"]).status_code == 409


def test_duplicate_receipt_barcode_409(client, fridge, monkeypatch):
    use_ocr(RECEIPT_ROWS)
    monkeypatch.setattr(barcode_service, "decode_receipt_barcode", lambda image: "2026101000120034")
    first = upload(client, fridge)
    assert first.status_code == 201
    assert first.json()["receipt_barcode"] == "2026101000120034"

    second = upload(client, fridge)
    assert second.status_code == 409
    assert second.json() == {"detail": "이미 등록된 영수증입니다.", "receipt_id": first.json()["id"]}
    assert len(uploaded_files()) == 1  # 방금 저장한 파일은 지운다

    # 다른 냉장고에는 같은 영수증을 올릴 수 있다
    other = client.post("/fridges", headers=fridge["headers"], json={"name": "회사"}).json()
    assert upload(client, {**fridge, "id": other["id"]}).status_code == 201

    # pending을 지우면 다시 올릴 수 있다
    assert client.delete(
        f"/fridges/{fridge['id']}/receipts/{first.json()['id']}", headers=fridge["headers"]
    ).status_code == 204
    assert len(uploaded_files()) == 1
    assert upload(client, fridge).status_code == 201


def test_unreadable_barcode_does_not_block_upload(client, fridge, monkeypatch):
    use_ocr(RECEIPT_ROWS)
    monkeypatch.setattr(barcode_service, "decode_receipt_barcode", lambda image: None)
    assert upload(client, fridge).status_code == 201
    assert upload(client, fridge).status_code == 201  # 바코드가 없으면 중복 검사를 하지 않는다
    with SessionLocal() as db:
        assert [receipt.receipt_barcode for receipt in db.scalars(select(Receipt))] == [None, None]


def test_upload_validation(client, fridge, monkeypatch):
    assert upload(client, fridge, name="receipt.pdf").status_code == 400
    assert upload(client, fridge, name="receipt.jpg", data=b"this is not an image").status_code == 400
    assert upload(client, fridge, name="receipt.png", data=image_bytes("PNG")).status_code == 201
    assert upload(client, fridge, name="receipt.webp", data=image_bytes("WEBP")).status_code == 201

    monkeypatch.setattr(settings, "max_upload_mb", 0)
    too_big = upload(client, fridge)
    assert too_big.status_code == 413
    assert "최대" in too_big.json()["detail"]
    assert len(uploaded_files()) == 2


def test_large_photo_is_resized(client, fridge):
    assert upload(client, fridge, data=image_bytes(size=(3000, 4000))).status_code == 201
    with Image.open(uploaded_files()[0]) as saved:
        assert saved.size == (1500, 2000)
        assert saved.format == "JPEG"


def test_no_items_warning_when_ocr_reads_nothing(client, fridge):
    # OCR_ENGINE=none (테스트 기본값): 초안이 비고 warnings가 붙는다
    response = upload(client, fridge)
    assert response.status_code == 201
    assert response.json()["draft_items"] == []
    assert response.json()["warnings"] == ["품목을 찾지 못했어요. 직접 추가해 주세요"]

    # 빈 초안에도 직접 품목을 넣어 확정할 수 있다. 구매일은 오늘.
    confirm = client.post(
        f"/fridges/{fridge['id']}/receipts/{response.json()['id']}/confirm",
        headers=fridge["headers"],
        json={"items": [{"name": "계란 30구", "quantity": 1, "amount": 7900, "discount": 0, "include": True}]},
    )
    assert confirm.status_code == 200
    (egg,) = confirm.json()["created_items"]
    assert egg["purchased_on"] == today().isoformat()
    assert egg["category"] == "egg" and egg["days_left"] == 30


def test_ocr_failure_falls_back_to_empty_draft(client, fridge):
    class BrokenEngine:
        def recognize(self, image_path):
            raise RuntimeError("model crashed")

    app.dependency_overrides[get_ocr_engine] = lambda: BrokenEngine()
    response = upload(client, fridge)
    assert response.status_code == 201
    assert response.json()["warnings"] == ["품목을 찾지 못했어요. 직접 추가해 주세요"]


def test_sum_mismatch_warning(client, fridge):
    use_ocr(
        [
            ["하나로마트"],
            ["상품명", "단가", "수량", "금액"],
            ["서울우유 1L", "2,980", "1", "2,980"],
            ["합계", "9,980"],  # 품목 합(2,980)과 1% 넘게 다르다
        ]
    )
    draft = upload(client, fridge).json()
    assert draft["items_sum"] == 2980
    assert draft["warnings"] == ["일부 품목을 못 읽었을 수 있어요"]
    assert draft["purchased_on"] is None


MISSING_TOFU = [row for row in RECEIPT_ROWS if row[0] != "풀무원 두부"]  # 빠른 모델이 한 줄을 놓친 결과


def use_two_models(fast, full) -> TwoModelOcrEngine:
    engine = TwoModelOcrEngine(fast, full)
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    return engine


def test_fast_model_result_is_kept_when_it_adds_up(client, fridge):
    engine = use_two_models(make_boxes(RECEIPT_ROWS), RuntimeError("the full model must not run"))
    draft = upload(client, fridge).json()
    assert engine.calls == [("fast", 0)]
    assert (len(draft["draft_items"]), draft["items_sum"], draft["warnings"]) == (4, 6970, [])


@pytest.mark.parametrize(
    "fast", [make_boxes(MISSING_TOFU), make_boxes([]), RuntimeError("fast model crashed")], ids=["mismatch", "empty", "error"]
)
def test_full_model_reads_again_when_the_fast_result_does_not_add_up(client, fridge, fast):
    engine = use_two_models(fast, make_boxes(RECEIPT_ROWS))
    response = upload(client, fridge)
    assert response.status_code == 201, response.text
    # 그 사이 다른 영수증이 들어와 있으면 끝나길 기다렸다가 다시 읽는다
    assert engine.calls == [("fast", 0), ("full", FULL_PASS_WAIT_SECONDS)]
    draft = response.json()
    assert (len(draft["draft_items"]), draft["items_sum"], draft["warnings"]) == (4, 6970, [])


def test_busy_fast_pass_is_503_without_a_second_read(client, fridge):
    engine = use_two_models(OcrBusyError("busy"), make_boxes(RECEIPT_ROWS))
    assert upload(client, fridge).status_code == 503
    assert engine.calls == [("fast", 0)]
    assert uploaded_files() == []


def test_missing_total_warning(client, fridge):
    use_ocr([["하나로마트"], ["상품명", "단가", "수량", "금액"], ["서울우유 1L", "2,980", "1", "2,980"]])
    draft = upload(client, fridge).json()
    assert draft["total_amount"] is None
    assert draft["warnings"] == ["영수증 총액을 찾지 못했어요. 빠진 품목이 없는지 확인해 주세요"]


def test_future_date_is_treated_as_misread(client, fridge):
    future = today() + timedelta(days=30)
    use_ocr([["CU 역삼점"], [future.strftime("%Y-%m-%d")], ["상품명"], ["우유", "2,980"], ["합계", "2,980"]])
    draft = upload(client, fridge).json()
    assert draft["purchased_on"] is None
    # 소비기한은 오늘 기준으로 추정한다
    assert draft["draft_items"][0]["expiry_date"] == (today() + timedelta(days=10)).isoformat()


def test_confirm_reestimates_untouched_expiry_when_purchase_date_changes(client, fridge):
    use_ocr(RECEIPT_ROWS)
    draft = upload(client, fridge).json()
    milk, tofu = draft["draft_items"][:2]
    response = client.post(
        f"/fridges/{fridge['id']}/receipts/{draft['id']}/confirm",
        headers=fridge["headers"],
        # 구매일을 고치고, 우유 소비기한은 초안 그대로, 두부는 line_no 없이 소비기한을 비워서 보낸다
        json={
            "purchased_on": "2026-10-12",
            "items": [milk, {"name": tofu["name"], "quantity": 2, "amount": 3000, "discount": 0, "include": True}],
        },
    )
    assert response.status_code == 200, response.text
    created = {item["name"]: item for item in response.json()["created_items"]}
    assert created["서울우유 1L"]["purchased_on"] == "2026-10-12"
    assert (created["서울우유 1L"]["expiry_date"], created["서울우유 1L"]["expiry_source"]) == ("2026-10-22", "category")
    assert created["풀무원 두부"]["expiry_date"] == "2026-10-19"


def test_confirm_validation(client, fridge):
    use_ocr(RECEIPT_ROWS)
    draft = upload(client, fridge).json()
    url = f"/fridges/{fridge['id']}/receipts/{draft['id']}/confirm"
    item = {"name": "우유", "quantity": 1, "amount": 1000, "discount": 0, "include": True}

    assert client.post(url, headers=fridge["headers"], json={"items": []}).status_code == 422
    too_much_discount = client.post(url, headers=fridge["headers"], json={"items": [{**item, "discount": 2000}]})
    assert too_much_discount.status_code == 422
    assert "할인" in too_much_discount.json()["detail"]
    assert client.post(url, headers=fridge["headers"], json={"items": [{**item, "quantity": 0}]}).status_code == 422
    assert client.post(url, headers=fridge["headers"], json={"items": [{**item, "category": "x"}]}).status_code == 422
    assert client.post(
        f"/fridges/{fridge['id']}/receipts/99999/confirm", headers=fridge["headers"], json={"items": [item]}
    ).status_code == 404
    # 실패한 요청은 영수증 상태를 바꾸지 않는다
    assert client.get(f"/fridges/{fridge['id']}/receipts/{draft['id']}", headers=fridge["headers"]).json()[
        "status"
    ] == "pending"


def test_item_code_on_receipt_triggers_product_lookup(client, fridge, monkeypatch):
    calls = []

    def fake_lookup(db, code):
        calls.append(code)
        return BarcodeLookup(
            "api",
            ProductInfo(code, name="풀무원 국산콩 두부", food_type="두부", shelf_life_text="제조일로부터 14일", shelf_life_days=14),
        )

    monkeypatch.setattr("app.routers.receipts.lookup_barcode", fake_lookup)
    use_ocr(
        [
            ["홈플러스 강서점"],
            ["2026.10.11"],
            ["상품명", "단가", "수량", "금액"],
            ["001 PB상품 300g"],
            ["8801114123456", "1,500", "2", "3,000"],
            ["서울우유 1L", "2,980", "1", "2,980"],
            ["합 계", "5,980"],
        ]
    )
    draft = upload(client, fridge).json()
    pb, milk = draft["draft_items"]
    assert calls == ["8801114123456"]  # 상품코드가 있는 줄만 조회한다
    assert pb["barcode"] == "8801114123456"
    assert pb["product_name"] == "풀무원 국산콩 두부"
    assert pb["category"] == "tofu"  # 영수증 이름으로는 모르지만 제품명으로 분류
    assert (pb["expiry_date"], pb["expiry_source"]) == ("2026-10-25", "barcode")
    assert (milk["barcode"], milk["product_name"], milk["expiry_source"]) == (None, None, "category")

    # 초안을 그대로 확정하면 barcode 출처가 유지된다 (앱의 "포장지 확인" 안내용)
    response = client.post(
        f"/fridges/{fridge['id']}/receipts/{draft['id']}/confirm",
        headers=fridge["headers"],
        json={"items": draft["draft_items"]},
    )
    created = response.json()["created_items"]
    assert (created[0]["expiry_date"], created[0]["expiry_source"]) == ("2026-10-25", "barcode")
    assert created[0]["barcode"] == "8801114123456"


def test_real_barcode_decoding():
    """zxing-cpp로 만든 Code128 바코드를 실제로 디코딩한다 (monkeypatch 없이)."""
    import zxingcpp

    code128 = zxingcpp.BarcodeFormat.Code128
    if hasattr(zxingcpp, "create_barcode"):  # zxing-cpp 3.x
        barcode_image = zxingcpp.create_barcode("2026101000120034", code128).to_image(scale=3)
    else:  # 2.x
        barcode_image = zxingcpp.write_barcode(code128, "2026101000120034", 480, 120)
    barcode = Image.fromarray(barcode_image).convert("RGB")
    receipt = Image.new("RGB", (barcode.width + 120, barcode.height + 400), "white")
    receipt.paste(barcode, (60, 340))
    assert barcode_service.decode_receipt_barcode(receipt) == "2026101000120034"
    assert barcode_service.decode_receipt_barcode(Image.new("RGB", (300, 300), "white")) is None
