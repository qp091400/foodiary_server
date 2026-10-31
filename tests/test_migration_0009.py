"""0009: 보기만 하던 사람은 고칠 수 있는 멤버가 되어 접근을 잃지 않고, 친구·변경 요청·초대코드 표는 지워진다."""

import os

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SINCE = "2026-10-20 09:00:00"


@pytest.fixture
def database(tmp_path, monkeypatch):
    """0008까지 올린 빈 SQLite. 테스트가 쓰는 메모리 DB와 따로 파일에 만든다."""
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(os.path.join(ROOT, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    command.upgrade(config, "0008")
    engine = sa.create_engine(url)
    yield config, engine
    engine.dispose()


def seed(engine):
    with engine.begin() as c:
        for uid, name in ((1, "owner"), (2, "viewer"), (3, "editor"), (4, "both")):
            c.execute(sa.text("INSERT INTO users (id, username, email, password_hash, nickname, token_version, created_at, friend_code, is_admin, comments_seen_id) "
                              "VALUES (:i, :n, :e, 'h', :n, 0, :t, :code, 0, 0)"), {"i": uid, "n": name, "e": f"{name}@x.com", "t": SINCE, "code": f"CODE000{uid}"})
        c.execute(sa.text("INSERT INTO fridges (id, name, owner_id, created_at, visibility) VALUES (1, 'f1', 1, :t, 'private'), (2, 'f2', 1, :t, 'friends')"), {"t": SINCE})
        c.execute(sa.text("INSERT INTO fridge_members (fridge_id, user_id, role, joined_at) VALUES "
                          "(1, 1, 'owner', :t), (1, 3, 'member', :t), (1, 4, 'member', :t), (2, 1, 'owner', :t)"), {"t": SINCE})
        # 보기 전용: 새 사람(2), 이미 멤버인 사람(4), 주인(1)까지 겹쳐 들어 있어도 멤버가 중복되면 안 된다
        c.execute(sa.text("INSERT INTO fridge_viewers (fridge_id, user_id, created_at) VALUES (1, 2, '2026-10-25 10:00:00'), (1, 4, '2026-10-26 10:00:00'), (1, 1, '2026-10-27 10:00:00'), (2, 2, '2026-10-28 10:00:00')"))
        c.execute(sa.text("INSERT INTO friendships (requester_id, addressee_id, status, created_at) VALUES (1, 2, 'accepted', :t)"), {"t": SINCE})
        c.execute(sa.text("INSERT INTO fridge_invites (fridge_id, code, created_by, expires_at) VALUES (1, 'INVITE22', 1, '2099-01-01')"))


def members(engine):
    with engine.connect() as c:
        return c.execute(sa.text("SELECT fridge_id, user_id, role, joined_at FROM fridge_members ORDER BY fridge_id, user_id")).fetchall()


def test_view_only_people_become_members_without_duplicates_and_keep_their_date(database):
    config, engine = database
    seed(engine)
    command.upgrade(config, "0009")
    assert [(f, u, r) for f, u, r, _ in members(engine)] == [
        (1, 1, "owner"), (1, 2, "member"), (1, 3, "member"), (1, 4, "member"),  # 보기 전용이던 2번이 들어왔고, 1번과 4번은 그대로 한 줄씩
        (2, 1, "owner"), (2, 2, "member"),
    ]
    joined = {(f, u): str(when) for f, u, _, when in members(engine)}
    assert joined[(1, 2)].startswith("2026-10-25 10:00") and joined[(2, 2)].startswith("2026-10-28 10:00")  # 보기 권한을 받은 날짜
    assert joined[(1, 4)].startswith("2026-10-20")  # 원래 멤버의 날짜는 바뀌지 않는다


def test_the_old_tables_and_columns_are_gone_and_the_new_ones_exist(database):
    config, engine = database
    seed(engine)
    command.upgrade(config, "0009")
    inspector = sa.inspect(engine)
    assert not {"friendships", "fridge_viewers", "fridge_share_links", "change_requests", "fridge_invites"} & set(inspector.get_table_names())
    assert "friend_code" not in {c["name"] for c in inspector.get_columns("users")}
    assert "share_token" in {c["name"] for c in inspector.get_columns("fridges")}
    with engine.connect() as c:
        assert c.execute(sa.text("SELECT id, visibility, share_token FROM fridges ORDER BY id")).fetchall() == [(1, "private", None), (2, "private", None)]  # '친구에게'는 '나만'으로


def test_the_head_is_a_single_chain_and_the_models_match_the_migrated_database(database):
    config, engine = database
    command.upgrade(config, "head")
    command.check(config)  # 모델과 DB가 다르면 예외
    from alembic.script import ScriptDirectory

    assert len(ScriptDirectory.from_config(config).get_heads()) == 1  # 시작점이 둘로 갈라지면 migrate 단계가 실패한다


def test_downgrade_keeps_the_new_members_and_brings_the_empty_tables_back(database):
    config, engine = database
    seed(engine)
    command.upgrade(config, "0009")
    command.downgrade(config, "0008")
    assert {"friendships", "fridge_viewers", "fridge_share_links", "change_requests", "fridge_invites"} <= set(sa.inspect(engine).get_table_names())
    assert (1, 2, "member") in [(f, u, r) for f, u, r, _ in members(engine)]  # 되돌려도 접근을 빼앗지 않는다
    command.upgrade(config, "0009")  # 다시 올려도 된다
