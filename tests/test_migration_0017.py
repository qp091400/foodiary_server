"""Remove the retired setting while preserving the account and related data."""
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


def test_household_removal_preserves_accounts_and_posts(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'remove-setting.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0016")
    engine = sa.create_engine(url)
    try:
        with engine.begin() as db:
            db.execute(sa.text("INSERT INTO users (id, username, email, password_hash, nickname, token_version, created_at, is_admin, comments_seen_id, household_size) "
                "VALUES (1, 'author', 'author@example.com', 'hash', 'author', 0, '2026-11-01 10:00:00', 0, 0, 6)"))
            db.execute(sa.text("INSERT INTO posts (id, author_id, title, body, created_at) "
                "VALUES (1, 1, '기존 글', '보존할 본문', '2026-11-01 11:00:00')"))
        command.upgrade(config, "head")
        command.check(config)
        assert "household_size" not in {c["name"] for c in sa.inspect(engine).get_columns("users")}
        with engine.connect() as db:
            assert db.scalar(sa.text("SELECT username FROM users WHERE id=1")) == "author"
            assert db.scalar(sa.text("SELECT body FROM posts WHERE id=1")) == "보존할 본문"
        command.downgrade(config, "0016")
        with engine.connect() as db:
            assert db.scalar(sa.text("SELECT household_size FROM users WHERE id=1")) is None
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
