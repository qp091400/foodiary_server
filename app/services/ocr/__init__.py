from app.config import settings
from app.services.ocr.base import NullOcrEngine, OcrBox, OcrEngine

__all__ = ["OcrBox", "OcrEngine", "NullOcrEngine", "get_ocr_engine"]


def get_ocr_engine() -> OcrEngine:
    """FastAPI dependency. 테스트에서는 app.dependency_overrides로 가짜 엔진을 끼운다."""
    if settings.ocr_engine.strip().lower() == "paddle":
        from app.services.ocr.paddle import get_paddle_engine

        return get_paddle_engine()
    return NullOcrEngine()
