"""PaddleOCR 엔진: paddle 없이도 돌도록 가짜 paddleocr 모듈을 끼워서 확인한다."""

import math
import sys
import types

import pytest
from PIL import Image

from app.config import settings
from app.services.ocr import NullOcrEngine, get_ocr_engine
from app.services.ocr.base import OcrBox
from app.services.ocr.paddle import PaddleOcrEngine, Verdict, boxes_from_results


class ArrayLike:
    """numpy 배열 흉내: tolist()가 있고 bool()로 평가하면 예외가 난다."""

    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values

    def __bool__(self):
        raise ValueError("The truth value of an array is ambiguous")


def test_boxes_from_dict_results_with_rec_boxes():
    results = [
        {
            "rec_texts": ["서울우유 1L", "  ", "2,980"],
            "rec_scores": ArrayLike([0.98, 0.5, 0.91]),
            "rec_boxes": ArrayLike([[10, 20, 110, 48], [0, 0, 5, 5], [300, 24, 380, 52]]),
        }
    ]
    assert boxes_from_results(results) == [
        OcrBox("서울우유 1L", 0.98, 10.0, 20.0, 110.0, 48.0),
        OcrBox("2,980", 0.91, 300.0, 24.0, 380.0, 52.0),  # 빈 글자는 버린다
    ]


def test_boxes_fall_back_to_polygons_and_json_res():
    class ResultObject:  # dict처럼 동작하지 않는 결과 객체 → .json["res"]
        json = {
            "res": {
                "rec_texts": ["두부", "좌표 없음"],
                "rec_scores": [0.9],
                "rec_polys": [[[10, 100], [80, 104], [79, 130], [9, 126]]],
            }
        }

    (box,) = boxes_from_results([ResultObject()])
    # 꼭짓점의 최소·최대, 좌표 없는 글자는 제외. 꼭짓점 4개로 기울기와 실제 높이도 잰다
    assert (box.text, box.score, box.x0, box.y0, box.x1, box.y1) == ("두부", 0.9, 9.0, 100.0, 80.0, 130.0)
    assert box.angle == pytest.approx(math.atan2(4, 70), abs=1e-3)
    assert box.height == pytest.approx(26.0, abs=0.1)


def test_boxes_from_unexpected_results():
    assert boxes_from_results(None) == []
    assert boxes_from_results([]) == []
    assert boxes_from_results([object(), 3, {"rec_texts": None}]) == []
    assert boxes_from_results([{"rec_texts": ["점수 없음"], "rec_boxes": [[0, 0, 10, 10]]}]) == [
        OcrBox("점수 없음", 0.0, 0.0, 0.0, 10.0, 10.0)
    ]


@pytest.fixture
def fake_paddleocr(monkeypatch):
    created = []

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def predict(self, image, **options):
            assert image.shape == (30, 40, 3)  # 경로가 아니라 BGR 배열을 넘긴다
            return [{"rec_texts": ["우유"], "rec_scores": [0.9], "rec_boxes": [[1, 2, 3, 4]]}]

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=FakePaddleOCR))
    return created


def test_engine_loads_lazily_once_with_requested_options(fake_paddleocr, tmp_path):
    pytest.importorskip("numpy")
    path = tmp_path / "영수증.jpg"
    Image.new("RGB", (40, 30), "white").save(path)

    engine = PaddleOcrEngine("korean")
    assert fake_paddleocr == []  # 만들기만 해서는 모델을 올리지 않는다
    assert engine.recognize(str(path)) == [OcrBox("우유", 0.9, 1.0, 2.0, 3.0, 4.0)]
    engine.recognize(str(path))
    assert fake_paddleocr == [  # 두 번 불러도 모델은 한 번만 만든다
        {
            "lang": "korean",
            "ocr_version": "PP-OCRv5",
            "text_detection_model_name": "PP-OCRv5_server_det",
            "text_recognition_model_name": "korean_PP-OCRv5_mobile_rec",
            "textline_orientation_model_name": "PP-LCNet_x1_0_textline_ori",
            "device": "cpu",
            "cpu_threads": 2,
            "enable_mkldnn": True,
            "use_doc_orientation_classify": True,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
            "doc_orientation_classify_model_name": "PP-LCNet_x1_0_doc_ori",
        }
    ]


def test_orientation_can_be_switched_off(fake_paddleocr, tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    path = tmp_path / "receipt.jpg"
    Image.new("RGB", (40, 30), "white").save(path)
    monkeypatch.setattr(settings, "ocr_orientation_model", "")

    PaddleOcrEngine("korean").recognize(str(path))
    assert fake_paddleocr[0]["use_doc_orientation_classify"] is False
    assert "doc_orientation_classify_model_name" not in fake_paddleocr[0]


@pytest.mark.parametrize(
    ("verdicts", "chosen", "reads"),
    [
        # 읽은 순서: 1 원본, 2 대비를 높인 사진, 3 90°, 4 270°, 5 180°
        ({1: Verdict.GOOD}, 1, 1),  # 합계가 맞으면 한 번으로 끝
        ({2: Verdict.GOOD}, 2, 2),  # 어두운 사진: 대비를 높여 맞으면 그것
        ({1: Verdict.UPRIGHT}, 1, 2),  # 방향은 맞음: 대비도 안 통하면 원래 결과
        ({2: Verdict.UPRIGHT}, 2, 2),  # 원래는 거의 못 읽었는데 대비를 높이니 방향이 맞아 보임
        ({4: Verdict.GOOD}, 4, 4),  # 가로 사진: 270° 돌려서 맞음
        ({}, 5, 5),  # 끝내 맞는 게 없으면 rank가 가장 큰 결과 (여기서는 마지막에 읽은 것)
    ],
    ids=["good", "contrast", "upright", "contrast-upright", "rotated", "none"],
)
def test_reads_again_with_more_contrast_then_rotated(monkeypatch, tmp_path, verdicts, chosen, reads):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    path = tmp_path / "landscape.jpg"
    Image.new("RGB", (40, 30), "white").save(path)  # 가로 사진: 높이 30, 너비 40
    calls = []

    class NumberedReads:
        """몇 번째로 읽었는지를 글자로 돌려주는 가짜 OCR."""

        def __init__(self, **kwargs):
            pass

        def predict(self, image, **options):
            calls.append((image.shape[:2], options.get("use_doc_orientation_classify")))
            return [{"rec_texts": [str(len(calls))], "rec_scores": [0.9], "rec_boxes": [[1, 2, 3, 4]]}]

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=NumberedReads))
    engine = PaddleOcrEngine("korean")
    assert engine.can_reread
    boxes = engine.recognize(
        str(path),
        judge=lambda boxes: verdicts.get(int(boxes[0].text), Verdict.DOUBTFUL),
        rank=lambda boxes: int(boxes[0].text),
    )
    assert boxes[0].text == str(chosen)
    assert len(calls) == reads
    # 원본과 대비를 높인 사진은 방향 판별을 거치고, 직접 돌려 읽을 때는 판별을 끈다
    expected = [((30, 40), None), ((30, 40), None), ((40, 30), False), ((40, 30), False), ((30, 40), False)]
    assert calls == expected[:reads]


def test_a_tie_keeps_the_first_reading(monkeypatch, tmp_path):
    """영수증이 아닌 사진(화면 캡처 등)은 어느 방향도 품목이 없다. 그때 거꾸로 돌려 읽은 결과가 뽑히면 안 된다."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    path = tmp_path / "screenshot.jpg"
    Image.new("RGB", (30, 40), "white").save(path)
    calls = []

    class MoreTextWhenRotated:
        def __init__(self, **kwargs):
            pass

        def predict(self, image, **options):
            calls.append(options)
            text = "화면" * (1 + len(calls) * 5)  # 돌려 읽을수록 글자 무게가 커진다
            return [{"rec_texts": [text], "rec_scores": [0.9], "rec_boxes": [[1, 2, 3, 4]]}]

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=MoreTextWhenRotated))
    engine = PaddleOcrEngine("korean")
    judge = lambda boxes: Verdict.DOUBTFUL  # noqa: E731
    boxes = engine.recognize(str(path), judge=judge, rank=lambda boxes: 0)
    assert len(calls) == 5 and boxes[0].text == "화면" * 6  # 5번 읽었지만 동점이면 처음 읽은 결과
    # rank가 없으면 글자 무게가 가장 큰 결과
    calls.clear()
    assert engine.recognize(str(path), judge=judge)[0].text == "화면" * 26


def test_reads_once_without_a_judge(fake_paddleocr, tmp_path):
    pytest.importorskip("numpy")
    path = tmp_path / "receipt.jpg"
    Image.new("RGB", (40, 30), "white").save(path)
    engine = PaddleOcrEngine("korean")
    assert len(engine.recognize(str(path))) == 1 and len(fake_paddleocr) == 1


def test_enhance_contrast_returns_a_grey_three_channel_image():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from app.services.ocr.paddle import enhance_contrast

    dim = np.full((40, 30, 3), (40, 50, 60), dtype=np.uint8)
    dim[10:20, 5:25] = (20, 25, 30)  # 어두운 바탕의 더 어두운 글자
    enhanced = enhance_contrast(dim)
    assert enhanced.shape == dim.shape
    assert (enhanced[..., 0] == enhanced[..., 1]).all() and (enhanced[..., 1] == enhanced[..., 2]).all()
    gray = cv2.cvtColor(dim, cv2.COLOR_BGR2GRAY)
    assert int(enhanced.max()) - int(enhanced.min()) > int(gray.max()) - int(gray.min())  # 대비가 커진다


def test_engine_detection_size_option(fake_paddleocr, tmp_path):
    pytest.importorskip("numpy")
    path = tmp_path / "receipt.jpg"
    Image.new("RGB", (40, 30), "white").save(path)

    PaddleOcrEngine("korean", det_max_side=1280).recognize(str(path))
    assert fake_paddleocr[0]["text_det_limit_side_len"] == 1280
    assert fake_paddleocr[0]["text_det_limit_type"] == "max"


def test_engine_selection(client):
    assert isinstance(get_ocr_engine(), NullOcrEngine)  # 테스트 환경은 OCR_ENGINE=none
    assert NullOcrEngine().recognize("anything.jpg") == []
