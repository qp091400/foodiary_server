"""0018은 표 하나만 더한다: 기존 데이터는 그대로이고, 되돌릴 수 있다."""
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


def test_name_corrections_table_is_added_and_removed_without_touching_data(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{(tmp_path / 'names.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "0017")
    engine = sa.create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(sa.text("INSERT INTO users (id, username, email, password_hash, nickname, token_version, created_at, is_admin, comments_seen_id) "
                              "VALUES (1, 'author', 'author@example.com', 'hash', 'author', 0, '2026-10-20 10:00:00', 0, 0)"))
        assert "name_corrections" not in sa.inspect(engine).get_table_names()

        command.upgrade(config, "head")
        command.upgrade(config, "head")  # 다시 해도 된다
        command.check(config)  # 모델과 마이그레이션이 같다

        columns = {column["name"]: column for column in sa.inspect(engine).get_columns("name_corrections")}
        assert set(columns) == {"id", "user_id", "seen", "fixed", "created_at"}
        assert all(not columns[name]["nullable"] for name in ("user_id", "seen", "fixed", "created_at"))
        assert any(index["name"] == "ix_name_corrections_seen" for index in sa.inspect(engine).get_indexes("name_corrections"))
        assert any(tuple(constraint["column_names"]) == ("user_id", "seen") for constraint in sa.inspect(engine).get_unique_constraints("name_corrections"))

        with engine.begin() as c:
            c.execute(sa.text("INSERT INTO name_corrections (user_id, seen, fixed, created_at) VALUES (1, '코카율라', '코카콜라', '2026-11-01 10:00:00')"))
        with engine.begin() as c:  # 같은 사용자의 같은 이름은 하나만
            try:
                c.execute(sa.text("INSERT INTO name_corrections (user_id, seen, fixed, created_at) VALUES (1, '코카율라', '코카쿨라', '2026-11-01 10:00:00')"))
                raise AssertionError("같은 사용자·같은 이름이 두 번 들어갔다")
            except sa.exc.IntegrityError:
                pass

        command.downgrade(config, "0017")
        assert "name_corrections" not in sa.inspect(engine).get_table_names()
        with engine.connect() as c:
            assert c.scalar(sa.text("SELECT COUNT(*) FROM users")) == 1
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
