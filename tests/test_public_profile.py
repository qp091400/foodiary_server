from tests.helpers import signup, auth, default_fridge_id

def test_profile_and_multiple_fridges_respect_visibility(client):
    owner, friend, stranger = [signup(client, name) for name in ("owner", "friend", "stranger")]
    uid = owner["user"]["id"]
    mine, buddy, outsider = auth(owner), auth(friend), auth(stranger)
    assert client.get(f"/users/{uid}/profile").status_code == 401
    profile = client.get(f"/users/{uid}/profile", headers=outsider).json()
    assert set(profile) == {"id", "nickname", "friend_code", "avatar_url", "is_friend"}
    assert profile["is_friend"] is False
    assert client.get("/users/999999/profile", headers=outsider).status_code == 404
    private_id = default_fridge_id(client, owner)
    public = client.post("/fridges", headers=mine, json={"name": "Public"}).json()["id"]
    friends = client.post("/fridges", headers=mine, json={"name": "Friends"}).json()["id"]
    for fridge, visibility in ((public, "public"), (friends, "friends")):
        assert client.patch(f"/fridges/{fridge}", headers=mine, json={"visibility": visibility}).status_code == 200
    link = client.post(f"/fridges/{friends}/share-links", headers=mine, json={}).json()["token"]
    assert client.post(f"/share-links/{link}/join", headers=buddy).status_code == 200
    assert client.get(f"/users/{uid}/profile", headers=buddy).json()["is_friend"] is True
    path = f"/users/{uid}/fridges"
    assert {r["id"] for r in client.get(path, headers=outsider).json()} == {public}
    assert {r["id"] for r in client.get(path, headers=buddy).json()} == {public, friends}
    assert {r["id"] for r in client.get(path, headers=mine).json()} == {private_id, public, friends}
    assert client.get(f"{path}/{friends}/items", headers=outsider).status_code == 404
    assert client.get(f"{path}/{private_id}/items", headers=buddy).status_code == 404
    assert client.patch(f"/fridges/{public}", headers=mine, json={"visibility": "private"}).status_code == 200
    assert client.get(f"{path}/{public}/items", headers=outsider).status_code == 404
