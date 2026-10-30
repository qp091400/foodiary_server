"""여러 사람이 동시에 올린 사진은 거절하지 않고 한 사람씩 차례로(사람 순서) 읽고, 엔진이 여러 개면 사진을 동시에 읽는다."""

import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.config import settings
from app.main import app
from app.routers import receipts
from app.services.ocr import get_ocr_engine
from app.services.ocr.base import OcrBusyError, next_ticket
from app.services.ocr.paddle import PaddleOcrEngine
from tests.helpers import auth, default_fridge_id, image_bytes, make_boxes, signup

ROWS = [["이마트 성수점"], ["상품명", "단가", "수량", "금액"], ["우유", "2,980", "1", "2,980"], ["합계", "2,980"]]


def start(target, *args):
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread


def wait_until(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "조건이 시간 안에 안 맞는다"
        time.sleep(0.005)


# ── 엔진 ────────────────────────────────────────────────────────────────


@pytest.fixture
def slow_paddleocr(monkeypatch):
    """predict가 gate가 열릴 때까지 멈춰 있는 가짜 PaddleOCR. 몇 번째로 읽었는지 기록한다."""
    state = SimpleNamespace(gate=threading.Event(), inside=threading.Event(), reads=[])

    class SlowPaddleOCR:
        def __init__(self, **kwargs):
            pass

        def predict(self, image, **options):
            state.reads.append(threading.current_thread().name)
            state.inside.set()
            assert state.gate.wait(10), "gate가 안 열렸다"
            return [{"rec_texts": ["우유"], "rec_scores": [0.9], "rec_boxes": [[1, 2, 3, 4]]}]

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=SlowPaddleOCR))
    return state


@pytest.fixture
def photo(tmp_path):
    pytest.importorskip("numpy")
    path = tmp_path / "receipt.jpg"
    Image.new("RGB", (40, 30), "white").save(path)
    return str(path)


def test_engine_makes_a_second_photo_wait_instead_of_failing(slow_paddleocr, photo):
    engine = PaddleOcrEngine("korean", engines=1)
    assert engine.can_queue and engine.engines == 1
    results = {}

    def read(name, wait):
        try:
            results[name] = engine.recognize(photo, wait=wait)
        except OcrBusyError:
            results[name] = "busy"

    first = start(read, "first", 0)
    assert slow_paddleocr.inside.wait(5)  # 첫 사진을 읽는 중
    assert engine.waiting == 0
    with pytest.raises(OcrBusyError):
        engine.recognize(photo)  # 기다리겠다고 하지 않으면 예전처럼 바로 거절
    assert engine.waiting == 0  # 거절된 사진은 줄에 서지 않는다
    second = start(read, "second", 10)
    wait_until(lambda: engine.waiting == 1)
    slow_paddleocr.gate.set()  # 첫 사진이 끝나면 둘째가 이어서 읽는다
    first.join(5)
    second.join(5)
    assert results["first"][0].text == "우유" and results["second"][0].text == "우유"
    assert len(slow_paddleocr.reads) == 2


def test_engine_gives_up_after_the_wait_and_when_the_line_is_full(slow_paddleocr, photo, monkeypatch):
    monkeypatch.setattr(settings, "ocr_queue_max", 1)
    engine = PaddleOcrEngine("korean", engines=1)
    first = start(engine.recognize, photo)
    assert slow_paddleocr.inside.wait(5)
    queued = start(lambda: engine.recognize(photo, wait=3))  # 줄의 하나뿐인 자리를 채운다
    wait_until(lambda: engine.waiting == 1)
    started = time.monotonic()
    with pytest.raises(OcrBusyError):
        engine.recognize(photo, wait=5)  # 줄이 가득 차서 5초를 기다리지 않고 바로 거절
    assert time.monotonic() - started < 1
    slow_paddleocr.gate.set()
    first.join(5)
    queued.join(5)


def test_engine_wait_runs_out_and_the_photo_leaves_the_line(slow_paddleocr, photo):
    engine = PaddleOcrEngine("korean", engines=1)
    first = start(engine.recognize, photo)
    assert slow_paddleocr.inside.wait(5)
    started = time.monotonic()
    with pytest.raises(OcrBusyError):
        engine.recognize(photo, wait=0.2)
    assert 0.15 <= time.monotonic() - started < 2
    assert engine.waiting == 0  # 시간이 지난 사진은 줄에서 빠졌다 (나중에 엔진이 비어도 읽지 않는다)
    slow_paddleocr.gate.set()
    first.join(5)
    time.sleep(0.1)
    assert len(slow_paddleocr.reads) == 1


def test_the_earlier_person_is_read_first_even_if_a_later_photo_arrives_before(photo):
    """사진마다 줄을 서면 사람들이 한 장씩 번갈아 읽혀서 모두 늦게 끝난다. 번호가 작은(먼저 온) 사람부터여야 한다."""
    engine = PaddleOcrEngine("korean", engines=1)
    order, gate = [], threading.Event()

    def fake_read(worker, image_path, judge, rank):
        order.append(image_path)
        if len(order) == 1:
            gate.wait(5)  # 첫 사진을 읽는 동안 다른 사진들이 줄을 선다
        return []

    engine._read = fake_read
    first, person_a, person_b = next_ticket(), next_ticket(), next_ticket()
    assert first < person_a < person_b
    threads = [start(lambda: engine.recognize("x", ticket=first))]
    wait_until(lambda: order == ["x"])
    for name, ticket in (("b1", person_b), ("a1", person_a), ("b2", person_b), ("a2", person_a)):
        threads.append(start(lambda name=name, ticket=ticket: engine.recognize(name, wait=10, ticket=ticket)))
        wait_until(lambda n=len(threads) - 1: engine.waiting == n)
    gate.set()
    for thread in threads:
        thread.join(5)
    assert order == ["x", "a1", "a2", "b1", "b2"]  # 같은 사람 안에서는 도착 순서


def test_two_engines_read_two_photos_at_the_same_time():
    engine = PaddleOcrEngine("korean", engines=2)
    both_inside = threading.Barrier(2, timeout=5)  # 두 사진이 동시에 읽는 중이어야만 통과한다
    seen = []

    def fake_read(worker, image_path, judge, rank):
        both_inside.wait()
        seen.append(threading.current_thread().name)
        return []

    engine._read = fake_read
    threads = [start(lambda n=n: engine.recognize(f"p{n}", wait=5)) for n in range(2)]
    for thread in threads:
        thread.join(8)
    assert len(seen) == 2 and not both_inside.broken
    assert len(set(seen)) == 2 and all(name.startswith("ocr") for name in seen)  # 엔진마다 자기 스레드


def test_each_engine_always_loads_and_reads_on_its_own_thread(monkeypatch, photo):
    """oneDNN은 스레드마다 캐시를 따로 둔다. 읽는 스레드가 바뀌면 읽기가 깨지고 메모리가 늘었다(실제 측정)."""
    models = []

    class RecordingPaddleOCR:
        def __init__(self, **kwargs):
            self.threads = [threading.current_thread().name]
            models.append(self)

        def predict(self, image, **options):
            self.threads.append(threading.current_thread().name)
            time.sleep(0.02)  # 엔진 둘이 번갈아 쓰이게 한다
            return [{"rec_texts": ["우유"], "rec_scores": [0.9], "rec_boxes": [[1, 2, 3, 4]]}]

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=RecordingPaddleOCR))
    engine = PaddleOcrEngine("korean", engines=2)
    callers = []

    def call():
        callers.append(threading.current_thread().name)  # 번호는 끝난 스레드가 다시 쓰므로 이름으로 구분한다
        engine.recognize(photo, wait=10)

    threads = [start(call) for _ in range(12)]  # 호출하는 스레드는 매번 새로 만든다 (요청마다 다른 작업 스레드)
    for thread in threads:
        thread.join(10)
    assert 1 <= len(models) <= 2
    assert sum(len(model.threads) - 1 for model in models) == 12
    for model in models:
        assert len(set(model.threads)) == 1  # 모델을 만든 스레드와 읽은 스레드가 같다
        assert model.threads[0].startswith("ocr") and model.threads[0] not in callers
    assert len({model.threads[0] for model in models}) == len(models)  # 엔진마다 다른 스레드


def test_warmup_reads_on_every_engine(monkeypatch):
    pytest.importorskip("numpy")
    models = []

    class WarmPaddleOCR:
        def __init__(self, **kwargs):
            models.append(self)

        def predict(self, image, **options):
            return [{"rec_texts": ["FOODIARY"], "rec_scores": [0.9], "rec_boxes": [[1, 2, 3, 4]]}]

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=WarmPaddleOCR))
    engine = PaddleOcrEngine("korean", engines=3)
    assert engine.ready is False
    engine.warmup()
    assert len(models) == 3 and engine.ready is True


def test_a_reading_error_reaches_the_caller_and_frees_the_engine(monkeypatch, photo):
    class Broken:
        def __init__(self, **kwargs):
            pass

        def predict(self, image, **options):
            raise RuntimeError("std::exception")

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=Broken))
    engine = PaddleOcrEngine("korean", engines=1)
    engine._workers[0].ready = True
    with pytest.raises(RuntimeError, match="std::exception"):
        engine.recognize(photo)
    assert engine.ready is False
    with pytest.raises(RuntimeError, match="std::exception"):  # 엔진이 잠겨 있지 않아서 바쁨(OcrBusyError)이 아니라 같은 오류가 다시 난다
        engine.recognize(photo)


def test_ticket_numbers_only_grow():
    assert next_ticket() < next_ticket()


# ── 업로드 ──────────────────────────────────────────────────────────────


def photo_bytes(number):
    """가로 크기로 몇 번째 사진인지 알 수 있는 사진."""
    return image_bytes(size=(100 + number, 150))


class QueueingEngine:
    """줄 서기를 지원하는 엔진 흉내. 받은 wait와 요청 번호를 기록하고, 읽을 때마다 가짜 시계를 흘려보낸다."""

    can_queue = True

    def __init__(self, clock=None, seconds_per_photo=0):
        self.waits = []
        self.tickets = []
        self.boxes = make_boxes(ROWS)
        self.clock = clock
        self.seconds_per_photo = seconds_per_photo

    def recognize(self, image_path, *, wait=0, ticket=None):
        self.waits.append(wait)
        self.tickets.append(ticket)
        if self.clock is not None:
            self.clock["now"] += self.seconds_per_photo
        return self.boxes


class ParallelEngine(QueueingEngine):
    """엔진이 여러 개인 흉내. 엔진 수만큼의 사진이 동시에 읽는 중이어야만 읽기가 끝난다."""

    def __init__(self, engines, hold=None):
        super().__init__()
        self.engines = engines
        self.hold = hold or {}  # 사진 번호 → 그 사진이 끝나기 전에 끝나야 하는 사진 번호
        self.finished = {}
        self.lock = threading.Lock()
        self.inside = self.most = 0
        self.places = threading.Semaphore(engines)  # 실제 엔진처럼 동시에 읽는 사진은 엔진 수까지
        self.together = threading.Barrier(engines, timeout=5) if engines > 1 else None

    def recognize(self, image_path, *, wait=0, ticket=None):
        with Image.open(image_path) as image:
            number = image.width - 100
        with self.places:
            with self.lock:
                self.inside += 1
                self.most = max(self.most, self.inside)
            try:
                if self.together and not self.hold:
                    self.together.wait()
                if number in self.hold:
                    assert self.finished.setdefault(self.hold[number], threading.Event()).wait(5)
                return super().recognize(image_path, wait=wait, ticket=ticket)
            finally:
                with self.lock:
                    self.inside -= 1
                self.finished.setdefault(number, threading.Event()).set()


@pytest.fixture
def fridge(client):
    tokens = signup(client, "minsu")
    return {"headers": auth(tokens), "id": default_fridge_id(client, tokens)}


def post_photos(client, fridge, count, path=""):
    files = [("files", (f"{n}.jpg", photo_bytes(n), "image/jpeg")) for n in range(count)]
    return client.post(f"/fridges/{fridge['id']}/receipts/batch{path}", headers=fridge["headers"], files=files)


def test_single_upload_waits_for_its_turn(client, fridge):
    engine = QueueingEngine()
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    response = client.post(f"/fridges/{fridge['id']}/receipts", headers=fridge["headers"],
                           files={"file": ("a.jpg", image_bytes(), "image/jpeg")})
    assert response.status_code == 201
    assert engine.waits == [settings.ocr_queue_wait_seconds] and engine.tickets == [None]  # 새 번호는 엔진이 정한다


def test_batch_has_no_five_photo_limit_and_all_photos_share_one_ticket(client, fridge):
    engine = QueueingEngine()
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    body = post_photos(client, fridge, 12).json()
    assert (body["succeeded"], body["failed"]) == (12, 0)
    assert len(engine.waits) == 12 and max(engine.waits) <= settings.ocr_queue_wait_seconds
    assert receipts.MAX_BATCH_FILES >= 20  # 예전 5장 제한이 아니다
    assert len(set(engine.tickets)) == 1 and engine.tickets[0] is not None  # 한 사람의 사진은 한 번호
    post_photos(client, fridge, 2)
    assert engine.tickets[-1] > engine.tickets[0]  # 다음에 온 요청은 더 큰 번호(뒷차례)


def test_a_batch_that_runs_out_of_time_returns_the_unstarted_photos_to_be_sent_again(client, fridge, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(receipts, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    monkeypatch.setattr(settings, "batch_seconds", 100)
    monkeypatch.setattr(settings, "ocr_queue_wait_seconds", 100)
    engine = QueueingEngine(clock, seconds_per_photo=40)  # 한 장에 40초(남의 사진을 기다린 시간 포함)
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    body = post_photos(client, fridge, 5).json()
    # 0초, 40초, 80초에 시작한 세 장은 읽고, 120초가 된 넷째부터는 시간이 없다
    assert [r["status_code"] for r in body["results"]] == [201, 201, 201, 503, 503]
    assert [r["retry_after"] for r in body["results"][3:]] == [receipts.BATCH_RETRY_SECONDS] * 2
    assert body["results"][3]["detail"] == receipts.BATCH_TIME_UP and body["results"][3]["receipt"] is None
    assert engine.waits == [100, 60, 20]  # 장마다 남은 시간을 넘지 않게 기다린다
    assert (body["succeeded"], body["failed"]) == (3, 2)
    # 못 읽은 사진은 아무것도 저장되지 않았으니 그대로 다시 올리면 된다
    clock["now"] = 5000.0
    again = post_photos(client, fridge, 2).json()
    assert [r["status_code"] for r in again["results"]] == [201, 201]


def test_test_page_batch_uses_the_same_queue_and_time_budget(client, monkeypatch):
    monkeypatch.setattr(settings, "dev_tools", True)
    clock = {"now": 0.0}
    monkeypatch.setattr(receipts, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    monkeypatch.setattr(settings, "batch_seconds", 50)
    engine = QueueingEngine(clock, seconds_per_photo=30)
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    files = [("files", (f"{n}.jpg", image_bytes(), "image/jpeg")) for n in range(3)]
    results = client.post("/dev/receipt-test/batch", files=files).json()["results"]
    assert [(r["status_code"], r["retry_after"]) for r in results] == [(201, None), (201, None), (503, 5)]
    assert engine.waits == [50, 20]


# ── 엔진이 여러 개일 때 사진 여러 장을 동시에 ──────────────────────────────


def test_a_batch_reads_as_many_photos_at_once_as_there_are_engines(client, fridge):
    engine = ParallelEngine(engines=3)
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    body = post_photos(client, fridge, 6).json()
    assert engine.most == 3  # 읽는 중인 사진이 엔진 수만큼 동시에 있었다 (아니면 위의 Barrier가 깨진다)
    assert (body["succeeded"], body["failed"]) == (6, 0)
    assert [r["index"] for r in body["results"]] == list(range(6))  # 결과는 보낸 순서
    assert len({r["receipt"]["id"] for r in body["results"]}) == 6  # 사진마다 초안 하나


def test_results_stay_in_sent_order_when_a_later_photo_finishes_first(client, fridge):
    engine = ParallelEngine(engines=2, hold={0: 1})  # 0번 사진은 1번 사진이 끝나야 끝난다
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    body = post_photos(client, fridge, 2).json()
    assert [(r["index"], r["status_code"]) for r in body["results"]] == [(0, 201), (1, 201)]
    assert engine.most == 2


def test_a_bad_photo_in_the_middle_fails_alone_and_a_busy_one_leaves_nothing_behind(client, fridge, monkeypatch):
    engine = ParallelEngine(engines=2, hold={0: 2})
    real = engine.recognize

    def sometimes_busy(image_path, *, wait=0, ticket=None):
        with Image.open(image_path) as image:
            if image.width - 100 == 3:
                raise OcrBusyError("busy")
        return real(image_path, wait=wait, ticket=ticket)

    engine.recognize = sometimes_busy
    app.dependency_overrides[get_ocr_engine] = lambda: engine
    files = [
        ("files", ("0.jpg", photo_bytes(0), "image/jpeg")),
        ("files", ("broken.jpg", b"not an image", "image/jpeg")),  # 1번: 사진이 아니다
        ("files", ("2.jpg", photo_bytes(2), "image/jpeg")),
        ("files", ("3.jpg", photo_bytes(3), "image/jpeg")),  # 3번: 엔진이 바쁘다고 거절
    ]
    body = client.post(f"/fridges/{fridge['id']}/receipts/batch", headers=fridge["headers"], files=files).json()
    assert [(r["index"], r["status_code"]) for r in body["results"]] == [(0, 201), (1, 400), (2, 201), (3, 503)]
    assert body["results"][3]["retry_after"] == 10
    saved = [path for path in Path(settings.upload_dir).rglob("*") if path.is_file()]
    assert len(saved) == 2  # 성공한 두 장만 남았다. 사진이 아닌 것과 거절된 것은 지워졌다
    assert len(client.get(f"/fridges/{fridge['id']}/receipts", headers=fridge["headers"]).json()) == 2


def test_two_people_uploading_at_once_are_read_person_by_person_not_alternately(client, fridge, monkeypatch):
    """한 사진이 끝나고 같은 사람의 다음 사진이 줄에 서기 전 틈에 다른 사람이 끼면, 두 사람이 한 장씩 번갈아 읽힌다(실측)."""
    engine = PaddleOcrEngine("korean", engines=1)
    boxes = make_boxes(ROWS)
    order = []

    def fake_read(worker, image_path, judge, rank):
        with Image.open(image_path) as image:
            order.append(image.width)
        time.sleep(0.05)
        return boxes

    engine._read = fake_read
    app.dependency_overrides[get_ocr_engine] = lambda: engine

    def upload(first_width, results):
        files = [("files", (f"{n}.jpg", image_bytes(size=(first_width + n, 150)), "image/jpeg")) for n in range(5)]
        response = client.post(f"/fridges/{fridge['id']}/receipts/batch", headers=fridge["headers"], files=files)
        results.append(response.status_code)

    statuses = []
    first = start(upload, 100, statuses)
    wait_until(lambda: len(order) >= 1)  # 첫 사람이 읽히기 시작한 뒤에 둘째 사람이 올린다
    second = start(upload, 200, statuses)
    first.join(20)
    second.join(20)
    assert statuses == [200, 200]
    assert order == [100, 101, 102, 103, 104, 200, 201, 202, 203, 204]
