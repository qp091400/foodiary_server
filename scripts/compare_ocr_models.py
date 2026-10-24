"""Compare the OCR detection models on every receipt photo in a folder (no DB or email).

Run it inside the dev image so the OCR packages exist. Put the photos in the gitignored
artifacts/receipts/ folder; the report contains the receipt text, so keep it in artifacts/ too.

  docker build -t foodiary-dev .
  docker run --rm --cpus=2 --memory=8g -v foodiary_models:/root/.paddlex \
    -v <repo>/artifacts/receipts:/data:ro -v <repo>/artifacts:/out -v <repo>/scripts:/work:ro \
    -e PYTHONPATH=/app -e OCR_DET_MAX_SIDE=1280 -e OCR_CPU_THREADS=2 -e OMP_NUM_THREADS=1 \
    foodiary-dev python /work/compare_ocr_models.py /data /out/ocr-ab-report.json

"sum==total" is only a proxy for completeness: it says the parsed items add up to the printed total.
"""

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.routers.receipts import load_image
from app.services.ocr.paddle import PaddleOcrEngine
from app.services.receipt_parser import parse_receipt

MODELS = ["PP-OCRv5_server_det", "PP-OCRv5_mobile_det"]
SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: compare_ocr_models.py <photo folder> <report.json>")
    folder, report_path = Path(sys.argv[1]), Path(sys.argv[2])
    images = sorted(path for path in folder.iterdir() if path.suffix.lower() in SUFFIXES)
    if not images:
        raise SystemExit("no photos found")
    print(f"photos: {len(images)}", flush=True)

    report = []
    with tempfile.TemporaryDirectory() as work:
        prepared = []
        for index, source in enumerate(images, 1):
            path = Path(work) / f"r{index}.jpg"
            load_image(source.read_bytes()).save(path, "JPEG", quality=90)
            prepared.append((index, source.name, path))
        for model in MODELS:
            settings.ocr_detection_model = model
            engine = PaddleOcrEngine(settings.ocr_lang, settings.ocr_det_max_side)
            engine.recognize(str(prepared[0][2]))  # loads the model; not timed
            for index, name, path in prepared:
                started = time.perf_counter()
                boxes = engine.recognize(str(path))
                seconds = time.perf_counter() - started
                parsed = parse_receipt(boxes)
                items_sum = sum(item.amount for item in parsed.items)
                total = parsed.total_amount
                report.append(dict(
                    photo=index, file=name, model=model, seconds=round(seconds, 1), boxes=len(boxes),
                    items=len(parsed.items), items_sum=items_sum, total=total,
                    sum_matches_total=bool(total and abs(items_sum - total) <= total * 0.01),
                    parsed=[(item.name, item.quantity, item.amount) for item in parsed.items],
                    raw_lines=parsed.lines,
                ))
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\nphoto | model                | sec  | boxes | items | sum    | total  | sum==total")
    for row in report:
        matches = "yes" if row["sum_matches_total"] else "NO"
        print(f"{row['photo']:5d} | {row['model']:<20} | {row['seconds']:4.1f} | {row['boxes']:5d} | "
              f"{row['items']:5d} | {row['items_sum']:6} | {str(row['total']):>6} | {matches}")
    for model in MODELS:
        rows = [row for row in report if row["model"] == model]
        matched = sum(row["sum_matches_total"] for row in rows)
        average = sum(row["seconds"] for row in rows) / len(rows)
        print(f"{model}: sum==total on {matched}/{len(rows)} photos, avg {average:.1f}s")


if __name__ == "__main__":
    main()
