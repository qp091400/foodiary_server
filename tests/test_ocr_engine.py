"""PaddleOCR 엔진: paddle 없이도 돌도록 가짜 paddleocr 모듈을 끼워서 확인한다."""

import sys
import types

import pytest
from PIL import Image

from app.services.ocr import NullOcrEngine, get_ocr_engine
from app.services.ocr.base import OcrBox
from app.services.ocr.paddle import PaddleOcrEngine, boxes_from_results


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
    assert box == OcrBox("두부", 0.9, 9.0, 100.0, 80.0, 130.0)  # 꼭짓점의 최소·최대, 좌표 없는 글자는 제외


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

        def predict(self, image):
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
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": True,
        }
    ]


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
