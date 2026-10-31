"""The post embedding migration only adds a table: existing posts are untouched and it can be reverted."""
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


def test_post_embeddings_table_is_added_and_removed_without_touching_posts(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'embeddings.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0012")
    engine = sa.create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(sa.text("INSERT INTO users (id, username, email, password_hash, nickname, token_version, created_at, is_admin, comments_seen_id) "
                              "VALUES (1, 'author', 'author@example.com', 'hash', 'author', 0, '2026-10-20 10:00:00', 0, 0)"))
            c.execute(sa.text("INSERT INTO posts (id, author_id, title, body, created_at) VALUES (1, 1, '기존 글', '기존 본문', '2026-10-20 11:00:00')"))
        assert "post_embeddings" not in sa.inspect(engine).get_table_names()
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        command.check(config)
        columns = {column["name"]: column for column in sa.inspect(engine).get_columns("post_embeddings")}
        assert set(columns) == {"post_id", "model", "content_hash", "vector", "updated_at"}
        assert not any(column["nullable"] for column in columns.values())
        with engine.connect() as c:
            assert c.scalar(sa.text("SELECT COUNT(*) FROM post_embeddings")) == 0  # 벡터는 검색할 때 만들어진다
            assert c.scalar(sa.text("SELECT title FROM posts WHERE id = 1")) == "기존 글"
        command.downgrade(config, "0012")
        assert "post_embeddings" not in sa.inspect(engine).get_table_names()
        with engine.connect() as c:
            assert c.scalar(sa.text("SELECT COUNT(*) FROM posts")) == 1
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
