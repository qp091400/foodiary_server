"""PaddleOCR(PP-OCRv5) 엔진. paddle은 처음 호출될 때만 import·로딩한다."""

import logging
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.ocr.base import OcrBox, OcrBusyError

logger = logging.getLogger(__name__)


def _as_list(value: Any) -> list:
    """numpy 배열·튜플·None을 모두 list로. (numpy 배열은 `or []`로 다룰 수 없다)"""
    if value is None:
        return []
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)


def _as_mapping(result: Any) -> Mapping | None:
    """PaddleOCR 3.x 결과 항목을 dict처럼 읽는다. 안 되면 .json["res"]를 시도한다."""
    if isinstance(result, Mapping) and "rec_texts" in result:
        return result
    payload = getattr(result, "json", None)
    if callable(payload):
        payload = payload()
    if isinstance(payload, Mapping):
        inner = payload.get("res", payload)
        if isinstance(inner, Mapping):
            return inner
    if isinstance(result, Mapping):
        inner = result.get("res")
        return inner if isinstance(inner, Mapping) else result
    return None


def _rect(box: Any, poly: Any) -> tuple[float, float, float, float] | None:
    """rec_boxes([x0, y0, x1, y1])를 쓰고, 없으면 rec_polys 꼭짓점으로 계산한다."""
    try:
        if box is not None and len(box) == 4 and not hasattr(box[0], "__len__"):
            x0, y0, x1, y1 = (float(v) for v in box)
            return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
        if poly is not None and len(poly) >= 3:
            xs = [float(point[0]) for point in poly]
            ys = [float(point[1]) for point in poly]
            return min(xs), min(ys), max(xs), max(ys)
    except (TypeError, ValueError, IndexError):
        pass
    return None


def boxes_from_results(results: Any) -> list[OcrBox]:
    """`PaddleOCR.predict()` 결과에서 OcrBox 목록을 꺼낸다. 형식이 달라도 죽지 않게 방어적으로 읽는다."""
    boxes: list[OcrBox] = []
    for result in results or []:
        data = _as_mapping(result)
        if data is None:
            logger.warning("알 수 없는 OCR 결과 형식: %s", type(result).__name__)
            continue
        texts = _as_list(data.get("rec_texts"))
        scores = _as_list(data.get("rec_scores"))
        rects = _as_list(data.get("rec_boxes"))
        polys = _as_list(data.get("rec_polys")) or _as_list(data.get("dt_polys"))
        for index, text in enumerate(texts):
            text = str(text).strip()
            if not text:
                continue
            rect = _rect(
                rects[index] if index < len(rects) else None,
                polys[index] if index < len(polys) else None,
            )
            if rect is None:
                continue  # 좌표가 없으면 줄을 묶을 수 없다
            try:
                score = float(scores[index]) if index < len(scores) else 0.0
            except (TypeError, ValueError):
                score = 0.0
            boxes.append(OcrBox(text, score, *rect))
    return boxes


class PaddleOcrEngine:
    def __init__(self, lang: str, det_max_side: int = 0) -> None:
        self._lang = lang
        self._det_max_side = det_max_side
        self._ocr: Any = None
        self._load_lock = threading.Lock()
        self._predict_lock = threading.Lock()  # paddle predictor는 스레드 안전하지 않다
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def _load(self) -> Any:
        if self._ocr is None:
            with self._load_lock:
                if self._ocr is None:
                    logger.info("PaddleOCR 모델 로딩 (lang=%s) - 처음 한 번은 모델을 내려받느라 오래 걸립니다", self._lang)
                    options = {}
                    if self._det_max_side > 0:
                        # 검출만 줄인 해상도로 한다. 글자 인식은 원본 해상도에서 잘라 쓰므로 품질 손실이 작다.
                        options = {"text_det_limit_side_len": self._det_max_side, "text_det_limit_type": "max"}
                    if settings.ocr_models_dir:
                        from app.services.ocr.manifest import verify_model_files

                        # 이미지 내 모델이 없거나 바뀌면 원격 다운로드로 대체하지 않는다.
                        verify_model_files()
                        root = Path(settings.ocr_models_dir)
                        options.update(
                            text_detection_model_dir=str(root / settings.ocr_detection_model),
                            text_recognition_model_dir=str(root / settings.ocr_recognition_model),
                            textline_orientation_model_dir=str(root / settings.ocr_textline_model),
                        )
                    from paddleocr import PaddleOCR  # 지연 import: 설치 안 된 환경에서도 서버·테스트가 뜬다

                    self._ocr = PaddleOCR(
                        lang=self._lang,
                        ocr_version=settings.ocr_version,
                        text_detection_model_name=settings.ocr_detection_model,
                        text_recognition_model_name=settings.ocr_recognition_model,
                        textline_orientation_model_name=settings.ocr_textline_model,
                        device="cpu",
                        cpu_threads=settings.ocr_cpu_threads,
                        enable_mkldnn=settings.ocr_enable_mkldnn,
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=True,
                        **options,
                    )
        return self._ocr

    def recognize(self, image_path: str) -> list[OcrBox]:
        import numpy as np
        from PIL import Image

        if not self._predict_lock.acquire(blocking=False):
            raise OcrBusyError("OCR is busy")
        try:
            ocr = self._load()
            # 경로 대신 배열: Windows의 비ASCII 경로도 지원한다.
            with Image.open(image_path) as image:
                bgr = np.ascontiguousarray(np.asarray(image.convert("RGB"))[:, :, ::-1])
            results = list(ocr.predict(bgr))
            boxes = boxes_from_results(results)
            self._ready = True
            return boxes
        except Exception:
            self._ready = False
            raise
        finally:
            self._predict_lock.release()

    def warmup(self) -> None:
        """설치 여부뿐 아니라 검출·방향·인식 모델의 실제 추론을 검사한다."""
        from PIL import Image, ImageDraw, ImageFont

        with tempfile.TemporaryDirectory(prefix="foodiary-ocr-") as folder:
            path = Path(folder) / "warmup.jpg"
            image = Image.new("RGB", (900, 220), "white")
            ImageDraw.Draw(image).text((40, 70), "FOODIARY OCR 12345", fill="black", font=ImageFont.load_default(size=48))
            image.save(path, "JPEG", quality=95)
            if not self.recognize(str(path)):
                self._ready = False
                raise RuntimeError("OCR warmup returned no text")
            if settings.app_env == "production":
                from app.services.ocr.manifest import verify_model_files

                try:
                    verify_model_files()
                except Exception:
                    self._ready = False
                    raise


_engine: PaddleOcrEngine | None = None
_engine_lock = threading.Lock()


def get_paddle_engine() -> PaddleOcrEngine:
    """모듈 전역 싱글톤."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = PaddleOcrEngine(settings.ocr_lang, settings.ocr_det_max_side)
    return _engine
