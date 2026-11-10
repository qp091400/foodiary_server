"""0014는 칸 하나와 표 하나만 더한다: 기존 글과 계정은 그대로이고, 되돌릴 수 있다."""
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


def test_link_preview_column_and_suspensions_table_are_added_and_removed_without_touching_data(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'suspensions.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0013")
    engine = sa.create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(sa.text("INSERT INTO users (id, username, email, password_hash, nickname, token_version, created_at, is_admin, comments_seen_id) "
                              "VALUES (1, 'author', 'author@example.com', 'hash', 'author', 0, '2026-10-20 10:00:00', 0, 0)"))
            c.execute(sa.text("INSERT INTO posts (id, author_id, title, body, created_at) VALUES (1, 1, '기존 글', '기존 본문 https://example.com', '2026-10-20 11:00:00')"))
        assert "community_suspensions" not in sa.inspect(engine).get_table_names()
        assert "link_preview" not in {column["name"] for column in sa.inspect(engine).get_columns("posts")}

        command.upgrade(config, "head")
        command.upgrade(config, "head")  # 다시 해도 된다
        command.check(config)  # 모델과 마이그레이션이 같다

        posts = {column["name"]: column for column in sa.inspect(engine).get_columns("posts")}
        assert posts["link_preview"]["nullable"] is True
        suspensions = {column["name"]: column for column in sa.inspect(engine).get_columns("community_suspensions")}
        assert set(suspensions) == {"id", "user_id", "created_by", "reason", "created_at", "ends_at", "lifted_at", "lifted_by"}
        required = {name for name, column in suspensions.items() if not column["nullable"]}
        assert {"user_id", "reason", "created_at", "ends_at"} <= required
        assert {"created_by", "lifted_at", "lifted_by"}.isdisjoint(required)
        assert any(index["name"] == "ix_community_suspensions_user_ends" for index in sa.inspect(engine).get_indexes("community_suspensions"))
        with engine.connect() as c:
            assert c.scalar(sa.text("SELECT COUNT(*) FROM community_suspensions")) == 0  # 지금 정지 중인 사람은 없다
            assert c.scalar(sa.text("SELECT link_preview FROM posts WHERE id = 1")) is None  # 옛 글에는 미리보기가 없다
            assert c.scalar(sa.text("SELECT title FROM posts WHERE id = 1")) == "기존 글"

        command.downgrade(config, "0013")
        assert "community_suspensions" not in sa.inspect(engine).get_table_names()
        assert "link_preview" not in {column["name"] for column in sa.inspect(engine).get_columns("posts")}
        with engine.connect() as c:
            assert c.scalar(sa.text("SELECT body FROM posts WHERE id = 1")) == "기존 본문 https://example.com"
            assert c.scalar(sa.text("SELECT COUNT(*) FROM users")) == 1
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
