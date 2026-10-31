"""사진에서 영수증 종이를 잘라 펴는 단계: 찾으면 쓰고, 못 찾거나 실패해도 원래 읽기 순서는 그대로다."""
import sys
import types

import pytest
from PIL import Image

from app.services.ocr import paddle
from app.services.ocr.paddle import PaddleOcrEngine, Verdict, crop_receipt

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")


def desk_photo(paper: list[list[int]], size=(1200, 900)):
    """어두운 책상 위에 밝은 종이가 놓인 사진."""
    canvas = np.full((size[0], size[1], 3), 40, np.uint8)
    cv2.fillPoly(canvas, [np.array(paper, np.int32)], (235, 235, 235))
    return canvas


def test_a_tilted_receipt_is_cut_out_and_straightened():
    photo = desk_photo([[220, 180], [700, 150], [740, 1050], [180, 1020]])
    cropped = crop_receipt(photo)
    assert cropped is not None
    height, width = cropped.shape[:2]
    assert 800 <= height <= 950 and 480 <= width <= 580 and height > width
    assert cropped.mean() > 200  # 책상이 거의 섞이지 않는다


@pytest.mark.parametrize(
    "photo",
    [
        np.full((1200, 900, 3), 40, np.uint8),  # 종이가 없다
        np.full((1200, 900, 3), 240, np.uint8),  # 사진 전체가 종이(스캔·화면 캡처)
        desk_photo([[400, 400], [500, 400], [500, 600], [400, 600]]),  # 너무 작다
        desk_photo([[100, 100], [800, 100], [800, 900], [100, 900]]),  # 정사각형에 가까워 영수증 모양이 아니다
    ],
    ids=["no-paper", "full-page", "small", "square"],
)
def test_nothing_is_cut_when_there_is_no_receipt_shaped_paper(photo):
    assert crop_receipt(photo) is None


def run(monkeypatch, tmp_path, crop):
    path = tmp_path / "photo.jpg"
    Image.fromarray(cv2.cvtColor(desk_photo([[220, 180], [700, 150], [740, 1050], [180, 1020]]), cv2.COLOR_BGR2RGB)).save(path)
    shapes = []

    class NumberedReads:
        def __init__(self, **kwargs):
            pass

        def predict(self, image, **options):
            shapes.append(image.shape[:2])
            return [{"rec_texts": [str(len(shapes))], "rec_scores": [0.9], "rec_boxes": [[1, 2, 3, 4]]}]

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=NumberedReads))
    if crop is not None:
        monkeypatch.setattr(paddle, "crop_receipt", crop)
    boxes = PaddleOcrEngine("korean").recognize(
        str(path), judge=lambda boxes: Verdict.GOOD if boxes[0].text == "2" else Verdict.DOUBTFUL, rank=lambda boxes: 0)
    return boxes[0].text, shapes


def test_the_cut_out_receipt_is_read_second_when_the_first_reading_is_not_good(monkeypatch, tmp_path):
    text, shapes = run(monkeypatch, tmp_path, None)
    assert text == "2" and len(shapes) == 2
    assert shapes[0] == (1200, 900) and shapes[1][0] > shapes[1][1] and shapes[1] != shapes[0]  # 두 번째는 잘라 편 사진


def test_a_failing_cut_does_not_break_reading(monkeypatch, tmp_path):
    def broken(_image):
        raise RuntimeError("cv2 failure")

    text, shapes = run(monkeypatch, tmp_path, broken)
    assert text == "2" and len(shapes) == 2 and shapes[1] == (1200, 900)  # 두 번째는 대비를 높인 원본 크기
