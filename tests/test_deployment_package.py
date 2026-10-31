"""배포 아카이브에 비밀값은 제외하고 Docker 제외 규칙은 반드시 포함한다."""

import sys
import tarfile

from scripts import package_release


def test_release_excludes_secrets_and_preserves_dockerignore(tmp_path, monkeypatch):
    monkeypatch.setattr(package_release, "ROOT", tmp_path)
    for filename in package_release.FILES:
        (tmp_path / filename).write_text("public deployment source", encoding="utf-8")
    for directory in package_release.DIRECTORIES:
        (tmp_path / directory).mkdir()
    (tmp_path / "app" / "main.py").write_text("# public source", encoding="utf-8")
    for name in (".env.production", "app/.env", "app/private.key", "app/private.pem", "deploy/client.secrets.json"):
        (tmp_path / name).write_text("SECRET CANARY", encoding="utf-8")
    (tmp_path / "deploy" / "cloudbuild.trigger.yaml").write_text("steps: []", encoding="utf-8")
    private = tmp_path / "app" / ".cloudrun" / "secrets"
    private.mkdir(parents=True)
    (private / "DATABASE_URL").write_text("SECRET CANARY", encoding="utf-8")
    output = tmp_path / "release.tar.gz"
    monkeypatch.setattr(sys, "argv", ["package_release", "--output", str(output)])
    package_release.main()
    with tarfile.open(output) as archive:
        names = set(archive.getnames())
        assert {".dockerignore", "app/main.py", "deploy/cloudbuild.trigger.yaml", "recipe-package.json"} <= names
        assert not {".env.production", "app/.env", "app/private.key", "app/private.pem"} & names
        assert "deploy/client.secrets.json" not in names
        assert "app/.cloudrun/secrets/DATABASE_URL" not in names
