"""Download and verify the three OCR models, then copy only verified model files."""

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.ocr.manifest import verify_model_files
from app.services.ocr.paddle import get_paddle_engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    get_paddle_engine().warmup()
    hashes = verify_model_files()
    source = Path(os.environ.get("PADDLE_PDX_CACHE_HOME", str(Path.home() / ".paddlex"))) / "official_models"
    for name in hashes:
        target = args.destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    verify_model_files(cache=args.destination)
    print(f"Baked {len(hashes)} verified OCR files. No user images or credentials are included.")


if __name__ == "__main__":
    main()
