"""OCR 엔진 인터페이스. 나중에 로컬 VLM 등으로 바꿀 때 이 Protocol만 지키면 된다."""

import itertools
from dataclasses import dataclass
from typing import Protocol


class OcrBusyError(RuntimeError):
    """이미 다른 영수증을 처리 중이다."""


_tickets = itertools.count()


def next_ticket() -> int:
    """요청 한 번이 받는 번호. 번호가 작은(먼저 온) 요청의 사진을 먼저 읽는다. 한 요청의 사진은 모두 같은 번호를 쓴다."""
    return next(_tickets)


@dataclass
class OcrBox:
    text: str
    score: float
    x0: float  # 글자 상자를 감싸는 수평 사각형
    y0: float
    x1: float
    y1: float
    angle: float = 0.0  # 글자 줄의 기울기(라디안, 오른쪽이 아래로 내려가면 +). 꼭짓점 4개가 있을 때만 잰다
    height: float = 0.0  # 기울기를 뺀 실제 글자 높이. 0이면 y1 - y0을 쓴다


class OcrEngine(Protocol):
    def recognize(self, image_path: str) -> list[OcrBox]: ...


class NullOcrEngine:
    """OCR_ENGINE=none: 아무것도 읽지 않는다 (초안이 비고 warnings가 붙는다)."""

    def recognize(self, image_path: str) -> list[OcrBox]:
        return []
