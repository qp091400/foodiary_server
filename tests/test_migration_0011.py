"""Existing posts and reviews survive the community board migration."""
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


def test_existing_reviews_are_backfilled_once_and_deleted_reviews_stay_hidden(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'boards.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0010")
    engine = sa.create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(sa.text("INSERT INTO users (id, username, email, password_hash, nickname, token_version, created_at, is_admin, comments_seen_id) "
                              "VALUES (1, 'author', 'author@example.com', 'hash', 'author', 0, '2026-10-20 10:00:00', 0, 0)"))
            c.execute(sa.text("INSERT INTO posts (id, author_id, title, body, created_at) VALUES (1, 1, '기존 글', '기존 본문', '2026-10-20 11:00:00')"))
            c.execute(sa.text("INSERT INTO recipe_reviews (id, author_id, recipe_id, recipe_name, rating, body, created_at, updated_at, deleted_at) VALUES "
                              "(1, 1, 'source:1', '요리', 4, '맛있어요', '2026-10-21 10:00:00', '2026-10-22 10:00:00', NULL), "
                              "(2, 1, 'source:2', '별점만', 3, NULL, '2026-10-23 10:00:00', NULL, NULL), "
                              "(3, 1, 'source:3', '삭제된 후기', 2, '삭제됨', '2026-10-24 10:00:00', NULL, '2026-10-25 10:00:00')"))
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        command.check(config)
        with engine.connect() as c:
            posts = c.execute(sa.text("SELECT * FROM posts ORDER BY id")).mappings().all()
            assert len(posts) == 3
            assert (posts[0]["title"], posts[0]["body"], posts[0]["board"], posts[0]["review_id"]) == ("기존 글", "기존 본문", "general", None)
            linked = {post["review_id"]: post for post in posts[1:]}
            assert set(linked) == {1, 2}
            assert linked[1]["board"] == "recipe" and linked[1]["body"] == "맛있어요"
            assert str(linked[1]["created_at"]).startswith("2026-10-21")
            assert str(linked[1]["updated_at"]).startswith("2026-10-22")
            assert linked[2]["body"] == "별점 3점을 남겼어요."
        command.downgrade(config, "0010")
        with engine.connect() as c:
            assert c.scalar(sa.text("SELECT COUNT(*) FROM posts")) == 1
            assert c.scalar(sa.text("SELECT COUNT(*) FROM recipe_reviews")) == 3
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
