"""0015는 칸 하나만 더한다: 기존 글은 그대로이고(미리보기 칸은 null), 되돌릴 수 있다."""
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings

OLD = {"url": "https://youtu.be/dQw4w9WgXcQ", "kind": "youtube", "title": "옛 영상", "description": None, "image": None, "site": "YouTube"}


def test_link_previews_column_is_added_and_removed_without_touching_data(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'previews.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0014")
    engine = sa.create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(sa.text("INSERT INTO users (id, username, email, password_hash, nickname, token_version, created_at, is_admin, comments_seen_id) "
                              "VALUES (1, 'author', 'author@example.com', 'hash', 'author', 0, '2026-10-20 10:00:00', 0, 0)"))
            c.execute(sa.text("INSERT INTO posts (id, author_id, title, body, created_at, link_preview) "
                              "VALUES (1, 1, '기존 글', '기존 본문 https://youtu.be/dQw4w9WgXcQ', '2026-10-20 11:00:00', :preview)"), {"preview": json.dumps(OLD)})
        assert "link_previews" not in {column["name"] for column in sa.inspect(engine).get_columns("posts")}

        command.upgrade(config, "head")
        command.upgrade(config, "head")  # 다시 해도 된다
        command.check(config)  # 모델과 마이그레이션이 같다

        posts = {column["name"]: column for column in sa.inspect(engine).get_columns("posts")}
        assert posts["link_previews"]["nullable"] is True
        with engine.connect() as c:
            assert c.scalar(sa.text("SELECT link_previews FROM posts WHERE id = 1")) is None  # 옛 글은 목록 칸이 비어 있고, 읽을 때 예전 칸을 쓴다
            assert json.loads(c.scalar(sa.text("SELECT link_preview FROM posts WHERE id = 1"))) == OLD  # 예전 칸은 그대로다

        command.downgrade(config, "0014")
        assert "link_previews" not in {column["name"] for column in sa.inspect(engine).get_columns("posts")}
        with engine.connect() as c:
            assert json.loads(c.scalar(sa.text("SELECT link_preview FROM posts WHERE id = 1"))) == OLD
            assert c.scalar(sa.text("SELECT body FROM posts WHERE id = 1")) == "기존 본문 https://youtu.be/dQw4w9WgXcQ"
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
