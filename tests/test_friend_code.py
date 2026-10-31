"""Retired friendship entry points must no longer be exposed."""
import pytest
from tests.helpers import signup, auth
from app.database import SessionLocal
from app.models import Friendship
from sqlalchemy import select, func

@pytest.mark.parametrize(("method", "path"), [("get", "/friends"), ("post", "/friends"), ("delete", "/friends/2"), ("post", "/friends/requests")])
def test_friend_endpoints_are_removed(client, method, path):
    user = signup(client)
    assert getattr(client, method)(path, headers=auth(user)).status_code == 404

def test_inviting_does_not_create_friendships(client):
    owner, guest = signup(client, "owner"), signup(client, "guest")
    fridge = client.get("/fridges", headers=auth(owner)).json()[0]["id"]
    link = client.post(f"/fridges/{fridge}/share-links", headers=auth(owner), json={}).json()
    result = client.post(f"/share-links/{link['token']}/join", headers=auth(guest))
    assert result.status_code == 200
    assert "newly_friends" not in result.json()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Friendship)) == 0

@pytest.mark.parametrize("permission", ["viewer", "editor"])
def test_participant_can_leave_and_rejoin_without_affecting_owner(client, permission):
    owner, guest = signup(client, "owner"), signup(client, "guest")
    fridge = client.get("/fridges", headers=auth(owner)).json()[0]["id"]
    link = client.post(f"/fridges/{fridge}/share-links", headers=auth(owner), json={"permission": permission}).json()
    assert client.post(f"/share-links/{link['token']}/join", headers=auth(guest)).status_code == 200
    listed = client.get("/shared-fridges", headers=auth(guest)).json()
    assert [(row["id"], row["my_permission"]) for row in listed] == [(fridge, "editor")]
    assert client.delete(f"/shared-fridges/{fridge}", headers=auth(owner)).status_code == 404
    assert client.delete(f"/shared-fridges/{fridge}", headers=auth(guest)).status_code == 204
    assert client.get("/shared-fridges", headers=auth(guest)).json() == []
    assert client.get(f"/users/{owner['user']['id']}/fridges/{fridge}/items", headers=auth(guest)).status_code == 404
    assert client.post(f"/share-links/{link['token']}/join", headers=auth(guest)).status_code == 200


def test_legacy_friend_visibility_is_closed_but_explicit_invitation_survives(client):
    from app.models import Fridge
    owner, guest, outsider = [signup(client, name) for name in ("owner", "guest", "outsider")]
    fridge = client.get("/fridges", headers=auth(owner)).json()[0]["id"]
    link = client.post(f"/fridges/{fridge}/share-links", headers=auth(owner), json={}).json()
    client.post(f"/share-links/{link['token']}/join", headers=auth(guest))
    with SessionLocal() as db:
        db.get(Fridge, fridge).visibility = "friends"
        db.add(Friendship(requester_id=owner['user']['id'], addressee_id=outsider['user']['id'], status="accepted"))
        db.commit()
    path = f"/users/{owner['user']['id']}/fridges"
    assert client.get(path, headers=auth(outsider)).json() == []
    assert client.get(path, headers=auth(guest)).json()[0]['visibility'] == 'private'
    assert client.get('/fridges', headers=auth(owner)).json()[0]['visibility'] == 'private'


def test_editors_cannot_bypass_owner_only_invites_with_legacy_codes(client):
    owner, editor = signup(client, "owner"), signup(client, "editor")
    fridge = client.get('/fridges', headers=auth(owner)).json()[0]['id']
    link = client.post(f'/fridges/{fridge}/share-links', headers=auth(owner), json={'permission': 'editor'}).json()
    client.post(f"/share-links/{link['token']}/join", headers=auth(editor))
    assert client.post(f'/fridges/{fridge}/invites', headers=auth(editor)).status_code == 403
