"""python -m scripts.import_recipes [--sample]. Atomic catalogue update, no secret logging."""
import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from app.config import settings
from app.services.recipes import SOURCE, SOURCE_URL, normalize


def fetch(key: str, start: int, end: int) -> dict:
    url = f"https://openapi.foodsafetykorea.go.kr/api/{key}/COOKRCP01/json/{start}/{end}"
    try:
        with urlopen(url, timeout=60) as response:
            data = json.load(response)
    except (URLError, TimeoutError, ValueError):
        raise RuntimeError("공공 API 응답을 받지 못했습니다. 기존 자료는 유지됩니다.") from None
    service = data.get("COOKRCP01", {})
    code = service.get("RESULT", data.get("RESULT", {})).get("CODE")
    if code != "INFO-000" or not service.get("row"):
        raise RuntimeError(f"공공 API 오류 ({code}). COOKRCP01 이용 신청/키/호출 한도를 확인하세요.")
    return service


def run(sample: bool):
    key = "sample" if sample else settings.foodsafety_api_key.strip()
    if not key:
        raise RuntimeError(".env의 FOODSAFETY_API_KEY에 COOKRCP01 인증키를 설정하세요. 샘플은 --sample.")
    first = fetch(key, 1, 5 if sample else 1000)
    total = int(first["total_count"])
    rows = list(first["row"])
    if not sample:
        for start in range(1001, total + 1, 1000):
            page = fetch(key, start, min(total, start + 999))
            if int(page["total_count"]) != total:
                raise RuntimeError("수집 중 원본 건수가 변경되었습니다. 다시 실행하세요.")
            rows.extend(page["row"])
        if len({r["RCP_SEQ"] for r in rows}) != total:
            raise RuntimeError("일부 자료가 누락됐습니다. 기존 자료는 유지됩니다.")
    recipes = [value for row in rows if (value := normalize(row))]
    if not recipes:
        raise RuntimeError("사용 가능한 조리 순서가 없습니다. 기존 자료는 유지됩니다.")
    root = Path(settings.recipe_data_dir)
    root.mkdir(parents=True, exist_ok=True)
    old = root / "catalogue.json"
    if sample and old.exists() and not json.loads(old.read_text(encoding="utf-8")).get("sample"):
        raise RuntimeError("전체 자료를 샘플로 덮어쓸 수 없습니다.")
    raw = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    (root / f"source-{digest}.json").write_bytes(raw)
    data = {"schema_version": 1, "source": SOURCE, "source_url": SOURCE_URL,
            "sample": sample, "source_total": total, "imported_count": len(recipes),
            "skipped_count": len(rows) - len(recipes), "sha256": digest,
            "imported_at": datetime.now(timezone.utc).isoformat(), "recipes": recipes}
    fd, temporary = tempfile.mkstemp(dir=root, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(data, target, ensure_ascii=False)
        os.replace(temporary, old)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(f"레시피 {len(recipes)}건 저장 / 원본 {total}건 / 샘플={sample} / SHA256={digest}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true")
    args = parser.parse_args()
    try:
        run(args.sample)
    except RuntimeError as error:
        parser.exit(1, str(error) + "\n")
