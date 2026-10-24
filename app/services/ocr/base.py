"""OCR 엔진 인터페이스. 나중에 로컬 VLM 등으로 바꿀 때 이 Protocol만 지키면 된다."""

from dataclasses import dataclass
from typing import Protocol


class OcrBusyError(RuntimeError):
    """이미 다른 영수증을 처리 중이다."""


@dataclass
class OcrBox:
    text: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float


class OcrEngine(Protocol):
    def recognize(self, image_path: str) -> list[OcrBox]: ...


class NullOcrEngine:
    """OCR_ENGINE=none: 아무것도 읽지 않는다 (초안이 비고 warnings가 붙는다)."""

    def recognize(self, image_path: str) -> list[OcrBox]:
        return []
