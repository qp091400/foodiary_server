"""Run real local inference and report timing, model hashes and parsed items (no DB or email)."""

import argparse
import json
import platform
import sys
import time
from pathlib import Path

try:
    import resource
except ImportError:  # Windows에서도 인식 검사는 가능하다.
    resource = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.services.ocr.paddle import get_paddle_engine
from app.services.ocr.manifest import verify_model_files
from app.services.receipt_parser import parse_receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", nargs="?", type=Path, help="A real receipt image; omitted = startup inference check")
    parser.add_argument("--output", type=Path, help="Optional local JSON report (contains OCR text)")
    args = parser.parse_args()
    engine = get_paddle_engine()
    started = time.perf_counter()
    if args.image:
        from app.routers.receipts import load_image
        import tempfile

        image = load_image(args.image.read_bytes())
        with tempfile.TemporaryDirectory(prefix="foodiary-check-") as folder:
            path = Path(folder) / "receipt.jpg"
            image.save(path, "JPEG", quality=90)
            boxes = engine.recognize(str(path))
        parsed = parse_receipt(boxes)
        result = {
            "recognized_boxes": len(boxes),
            "items": [vars(item) for item in parsed.items],
            "raw_lines": parsed.lines,
            "total_amount": parsed.total_amount,
            "purchased_on": parsed.purchased_on.isoformat() if parsed.purchased_on else None,
        }
        if not boxes:
            raise SystemExit("OCR ran but detected no text. Check the image and model before deployment.")
    else:
        engine.warmup()
        result = {"warmup": "passed"}
    hashes = verify_model_files()
    result.update(
        architecture=platform.machine(),
        elapsed_seconds=round(time.perf_counter() - started, 2),
        peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1) if resource else None,
        detection_max_side=settings.ocr_det_max_side,
        model_sha256=hashes,
    )
    report = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")
        print(f"Saved OCR report to {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
