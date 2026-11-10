"""0020은 칸 하나만 더한다: 기존 계정은 모두 테스트 계정이 아니고, 되돌릴 수 있다."""
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


def test_is_test_column_is_added_with_false_for_existing_users_and_can_be_removed(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'test-accounts.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0019")
    engine = sa.create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(sa.text("INSERT INTO users (id, username, email, password_hash, nickname, token_version, created_at, is_admin, comments_seen_id) "
                              "VALUES (1, 'author', 'author@example.com', 'hash', 'author', 0, '2026-10-20 10:00:00', 0, 0)"))
        assert "is_test" not in {column["name"] for column in sa.inspect(engine).get_columns("users")}

        command.upgrade(config, "head")
        command.upgrade(config, "head")  # 다시 해도 된다
        command.check(config)  # 모델과 마이그레이션이 같다

        columns = {column["name"]: column for column in sa.inspect(engine).get_columns("users")}
        assert columns["is_test"]["nullable"] is False
        with engine.connect() as c:
            assert c.scalar(sa.text("SELECT is_test FROM users WHERE id = 1")) in (0, False)  # 기존 계정은 테스트 계정이 아니다
            assert c.scalar(sa.text("SELECT username FROM users WHERE id = 1")) == "author"

        command.downgrade(config, "0019")
        assert "is_test" not in {column["name"] for column in sa.inspect(engine).get_columns("users")}
        with engine.connect() as c:
            assert c.scalar(sa.text("SELECT COUNT(*) FROM users")) == 1
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
