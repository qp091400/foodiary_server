"""PaddleOCR(PP-OCRv5) 엔진. paddle은 처음 호출될 때만 import·로딩한다."""

import logging
import math
import tempfile
import threading
from collections.abc import Callable, Mapping
from enum import IntEnum
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


def _slant(poly: Any) -> tuple[float, float]:
    """꼭짓점 4개(왼쪽 위부터 시계 방향)로 글자 줄의 기울기와 실제 높이를 잰다. 못 재면 (0, 0)."""
    try:
        if poly is None or len(poly) != 4:
            return 0.0, 0.0
        (ax, ay), (bx, by), (cx, cy), (dx, dy) = ((float(p[0]), float(p[1])) for p in poly)
    except (TypeError, ValueError, IndexError):
        return 0.0, 0.0
    angle = math.atan2((by - ay + cy - dy) / 2, (bx - ax + cx - dx) / 2)  # 윗변과 아랫변의 평균 방향
    height = (math.hypot(dx - ax, dy - ay) + math.hypot(cx - bx, cy - by)) / 2
    return angle, height


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
            poly = polys[index] if index < len(polys) else None
            rect = _rect(rects[index] if index < len(rects) else None, poly)
            if rect is None:
                continue  # 좌표가 없으면 줄을 묶을 수 없다
            try:
                score = float(scores[index]) if index < len(scores) else 0.0
            except (TypeError, ValueError):
                score = 0.0
            boxes.append(OcrBox(text, score, *rect, *_slant(poly)))
    return boxes


def text_weight(boxes: list[OcrBox]) -> float:
    """읽은 글자 수를 확신도로 곱해 더한 값. 방향이 맞는 사진일수록 크다."""
    return sum(box.score * len(box.text) for box in boxes)


class Verdict(IntEnum):
    """호출한 쪽이 읽은 결과를 판정한 값."""

    DOUBTFUL = 0  # 사진이 돌아갔거나 거의 못 읽었을 수 있다
    UPRIGHT = 1  # 방향은 맞아 보이지만 합계가 맞지 않는다
    GOOD = 2  # 합계까지 맞는다


def enhance_contrast(bgr: Any) -> Any:
    """흑백으로 바꾸고 구역별 히스토그램 평활화(CLAHE)를 한다. 어둡거나 흐린 사진용.

    실제 영수증 6장을 어둡게 만들었을 때 그냥 읽으면 3장, 이렇게 읽으면 6장이 맞았다.
    멀쩡한 사진에는 오히려 해로워서(6장 중 4장만 그대로) 첫 결과가 맞지 않을 때만 쓴다.
    """
    import cv2

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray), cv2.COLOR_GRAY2BGR)


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

    @property
    def can_reread(self) -> bool:
        """recognize(judge=...)로 대비를 높이거나 사진을 돌려 다시 읽을 수 있다."""
        return True

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
                        if settings.ocr_orientation_model:
                            options["doc_orientation_classify_model_dir"] = str(root / settings.ocr_orientation_model)
                    if settings.ocr_orientation_model:
                        # 가로로 찍거나 뒤집어 찍은 사진을 먼저 바로 세운다 (0·90·180·270°)
                        options["doc_orientation_classify_model_name"] = settings.ocr_orientation_model
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
                        use_doc_orientation_classify=bool(settings.ocr_orientation_model),
                        use_doc_unwarping=False,
                        use_textline_orientation=True,
                        **options,
                    )
        return self._ocr

    def recognize(
        self,
        image_path: str,
        *,
        judge: Callable[[list[OcrBox]], Verdict] | None = None,
        rank: Callable[[list[OcrBox]], Any] | None = None,
    ) -> list[OcrBox]:
        """사진을 읽는다. 방향 판별 모델이 사진을 먼저 바로 세운다.

        judge가 있고 결과가 GOOD이 아니면 다시 읽어 본다 (모두 같은 잠금 안에서).
        1. 대비를 높인 사진으로 한 번 더 읽는다. GOOD이면 그것을 쓴다.
        2. 둘 중 하나라도 UPRIGHT면 방향은 맞다. 원래 결과를(원래가 DOUBTFUL이면 대비를 높인 결과를) 쓴다.
        3. 둘 다 DOUBTFUL이면 방향 판별이 틀렸을 수 있다. 사진을 90°, 270°, 180° 돌려(판별 없이) 읽고 GOOD인 첫 결과를 쓴다.
        4. 끝내 GOOD이 없으면 rank가 가장 큰 결과 (rank가 없으면 글자를 가장 많이·확실하게 읽은 결과).
        """
        import numpy as np
        from PIL import Image

        if not self._predict_lock.acquire(blocking=False):
            raise OcrBusyError("OCR is busy")
        try:
            ocr = self._load()
            # 경로 대신 배열: Windows의 비ASCII 경로도 지원한다.
            with Image.open(image_path) as image:
                bgr = np.ascontiguousarray(np.asarray(image.convert("RGB"))[:, :, ::-1])
            if judge is None:
                boxes = boxes_from_results(list(ocr.predict(bgr)))
            else:
                boxes = self._read_carefully(ocr, bgr, judge, rank or text_weight)
            self._ready = True
            return boxes
        except Exception:
            self._ready = False
            raise
        finally:
            self._predict_lock.release()

    @staticmethod
    def _read_carefully(
        ocr: Any, bgr: Any, judge: Callable[[list[OcrBox]], Verdict], rank: Callable[[list[OcrBox]], Any]
    ) -> list[OcrBox]:
        """recognize의 1~4단계."""
        import numpy as np

        def read(image: Any, **options: Any) -> list[OcrBox]:
            return boxes_from_results(list(ocr.predict(np.ascontiguousarray(image), **options)))

        first = read(bgr)
        first_verdict = judge(first)
        if first_verdict == Verdict.GOOD:
            return first
        enhanced = read(enhance_contrast(bgr))
        enhanced_verdict = judge(enhanced)
        if enhanced_verdict == Verdict.GOOD:
            return enhanced
        if first_verdict == Verdict.UPRIGHT:
            return first
        if enhanced_verdict == Verdict.UPRIGHT:
            return enhanced
        candidates = [first, enhanced]
        for turns in (1, 3, 2):  # 반시계 방향으로 90°씩: 90°, 270°, 180°
            candidate = read(np.rot90(bgr, turns), use_doc_orientation_classify=False)
            if judge(candidate) == Verdict.GOOD:
                return candidate
            candidates.append(candidate)
        return max(candidates, key=rank)

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
