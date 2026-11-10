"""Read-only checks against a deployed HTTPS API; never submits credentials or images."""

import argparse
import sys
from urllib.parse import urlsplit

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="https://api.foodiary.kr")
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {"", "/", "/api", "/api/"}:
        parser.error("Supply an HTTPS origin, optionally ending in /api, without credentials, query strings or fragments")
    base = args.base_url.rstrip("/")
    failed = []
    with httpx.Client(timeout=15, follow_redirects=False) as client:
        for path, expected in (("/ready", 200), ("/fridges", 401), ("/docs", 404), ("/openapi.json", 404), ("/dev/receipt-test", 404)):
            response = client.get(base + path)
            if response.status_code != expected:
                failed.append(f"{path}: expected {expected}, got {response.status_code}")
            if path == "/ready":
                if response.status_code == 200 and response.json().get("ocr") != "ready":
                    failed.append("OCR has not passed readiness checks")
                if "strict-transport-security" not in response.headers:
                    failed.append("HTTPS proxy did not return HSTS")
            print(f"{path}: HTTP {response.status_code}")
    if failed:
        for message in failed:
            print(message, file=sys.stderr)
        raise SystemExit(1)
    print("Deployment checks passed. Verify actual signup email and receipt recognition separately.")


if __name__ == "__main__":
    main()
