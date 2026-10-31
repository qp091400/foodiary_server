"""테스트 공용 도우미."""

import io

from PIL import Image

from app.services import email
from app.services.ocr.base import OcrBox

PASSWORD = "pass1234"


def last_code(address: str, purpose: str) -> str:
    """console 메일함(OUTBOX)에서 가장 최근 인증코드를 꺼낸다."""
    for mail in reversed(email.OUTBOX):
        if mail["to"] == address and mail["purpose"] == purpose:
            return mail["code"]
    raise AssertionError(f"{address} / {purpose} 인증 메일이 없습니다")


def get_verification_token(client, address: str, purpose: str, username: str | None = None) -> str:
    """코드 발송 → OUTBOX에서 코드 추출 → verify → 인증 토큰."""
    body = {"email": address, "purpose": purpose}
    if username:
        body["username"] = username
    response = client.post("/auth/email/send-code", json=body)
    assert response.status_code == 200, response.text
    response = client.post(
        "/auth/email/verify", json={"email": address, "purpose": purpose, "code": last_code(address, purpose)}
    )
    assert response.status_code == 200, response.text
    return response.json()["verification_token"]


def signup(client, username: str = "minsu", address: str | None = None, nickname: str | None = None) -> dict:
    """회원가입 3단계를 거쳐 가입 응답(토큰 + user)을 돌려준다."""
    address = address or f"{username}@example.com"
    response = client.post(
        "/auth/signup",
        json={
            "username": username,
            "email": address,
            "password": PASSWORD,
            "nickname": nickname or username,
            "household_size": 1,
            "verification_token": get_verification_token(client, address, "signup"),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def default_fridge_id(client, tokens: dict) -> int:
    return client.get("/fridges", headers=auth(tokens)).json()[0]["id"]


def make_boxes(rows: list[list[str]], *, score: float = 0.95) -> list[OcrBox]:
    """줄(칸 목록)들을 영수증처럼 배치한 가짜 OCR 박스로 만든다. 순서는 일부러 뒤섞는다."""
    boxes = []
    for row_index, cells in enumerate(rows):
        top = 20 + row_index * 40
        for cell_index, text in enumerate(cells):
            left = 10 + cell_index * 180
            jitter = (cell_index % 2) * 3  # 같은 줄이어도 y가 조금씩 다르다
            boxes.append(OcrBox(text, score, left, top + jitter, left + 160, top + 28 + jitter))
    return boxes[::-1]


class FakeOcrEngine:
    def __init__(self, boxes: list[OcrBox]) -> None:
        self.boxes = boxes
        self.calls: list[str] = []

    def recognize(self, image_path: str) -> list[OcrBox]:
        self.calls.append(image_path)
        return self.boxes


def image_bytes(fmt: str = "JPEG", size: tuple[int, int] = (600, 900)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, fmt)
    return buffer.getvalue()
