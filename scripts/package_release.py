"""Package deployable source only; exclude credentials, uploads, caches and local data."""

import argparse
import hashlib
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ("app", "alembic", "scripts", "deploy")
FILES = (".dockerignore", ".gcloudignore", "Dockerfile", "compose.production.yml", "requirements.txt", "requirements-ocr.txt", "constraints-production.txt", "alembic.ini", "README.md", "recipe-package.json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "foodiary-oracle.tar.gz")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    paths = [ROOT / name for name in FILES]
    for directory in DIRECTORIES:
        paths.extend(path for path in (ROOT / directory).rglob("*") if path.is_file())
    with tarfile.open(args.output, "w:gz") as archive:
        for path in sorted(paths):
            relative = path.relative_to(ROOT)
            if any(part in {"__pycache__", ".cloudrun"} for part in relative.parts) or path.suffix in {".pyc", ".key", ".pem"}:
                continue
            if path.name.endswith(".secrets.json"):
                continue
            if path.name.startswith(".env") and path.name != ".env.production.example":
                continue
            archive.add(path, arcname=relative.as_posix(), recursive=False)
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(f"Created {args.output}\nSHA256 {digest}")


if __name__ == "__main__":
    main()
