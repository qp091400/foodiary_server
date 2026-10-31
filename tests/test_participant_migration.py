"""Existing invitations become editing memberships without duplicating owners or members."""
import importlib.util
from pathlib import Path

from sqlalchemy import select, text

from app.database import SessionLocal
from app.models import FridgeMember, FridgeViewer, FridgeShareLink
from app.timeutil import utcnow
from tests.helpers import signup, auth, default_fridge_id


def test_existing_viewers_and_links_migrate_to_editing_participants(client, monkeypatch):
    owner, guest, existing = [signup(client, name) for name in ('owner', 'guest', 'existing')]
    fridge = default_fridge_id(client, owner)
    spec = importlib.util.spec_from_file_location('participants_migration', Path(__file__).parents[1] / 'alembic/versions/0009_all_participants_edit.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    since = utcnow()
    with SessionLocal() as db:
        db.add(FridgeMember(fridge_id=fridge, user_id=existing['user']['id'], role='member'))
        for person in (owner, guest, existing):
            db.add(FridgeViewer(fridge_id=fridge, user_id=person['user']['id'], created_at=since))
        db.add(FridgeShareLink(fridge_id=fridge, token='legacy_view_only_invite', permission='viewer', expires_at=since, use_count=3, revoked_at=since))
        db.commit()
        monkeypatch.setattr(migration.op, 'execute', lambda sql: db.execute(text(sql)))
        migration.upgrade()
        db.commit()
        members = db.scalars(select(FridgeMember).where(FridgeMember.fridge_id == fridge)).all()
        assert sorted(m.role for m in members) == ['member', 'member', 'owner']
        assert next(m.joined_at for m in members if m.user_id == guest['user']['id']) == since
        assert db.scalars(select(FridgeViewer)).all() == []
        link = db.scalar(select(FridgeShareLink).where(FridgeShareLink.token == 'legacy_view_only_invite'))
        assert (link.permission, link.use_count, link.revoked_at) == ('editor', 3, since)
        migration.upgrade()  # Defensive: repeated application never duplicates memberships.
        db.commit()
    assert fridge in [f['id'] for f in client.get('/fridges', headers=auth(guest)).json()]
    assert client.post(f'/fridges/{fridge}/items', headers=auth(guest), json={'name': '함께 넣는 우유'}).status_code == 201
    assert client.delete(f'/fridges/{fridge}/members/{existing["user"]["id"]}', headers=auth(guest)).status_code == 403
    assert client.delete(f'/fridges/{fridge}/members/{guest["user"]["id"]}', headers=auth(owner)).status_code == 204
    assert client.post(f'/fridges/{fridge}/items', headers=auth(guest), json={'name': '이제 접근 불가'}).status_code == 404
