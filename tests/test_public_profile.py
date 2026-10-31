from tests.helpers import signup, auth, default_fridge_id


def test_profile_and_multiple_fridges_respect_visibility(client):
    owner, member, stranger = [signup(client, name) for name in ("owner", "member", "stranger")]
    uid = owner["user"]["id"]
    mine, joined, outsider = auth(owner), auth(member), auth(stranger)
    assert client.get(f"/users/{uid}/profile").status_code == 401
    profile = client.get(f"/users/{uid}/profile", headers=outsider).json()
    assert profile == {"id": uid, "nickname": "owner", "avatar_url": None}  # 친구 코드나 친구 여부는 없다
    assert client.get("/users/999999/profile", headers=outsider).status_code == 404
    private_id = default_fridge_id(client, owner)
    public = client.post("/fridges", headers=mine, json={"name": "Public"}).json()["id"]
    shared = client.post("/fridges", headers=mine, json={"name": "Shared"}).json()["id"]
    assert client.patch(f"/fridges/{public}", headers=mine, json={"visibility": "public"}).status_code == 200
    token = client.post(f"/fridges/{shared}/share-link", headers=mine).json()["token"]
    assert client.post(f"/share-links/{token}/join", headers=joined).status_code == 200
    path = f"/users/{uid}/fridges"
    assert {r["id"] for r in client.get(path, headers=outsider).json()} == {public}
    assert {r["id"]: r["my_permission"] for r in client.get(path, headers=joined).json()} == {public: "viewer", shared: "editor"}
    assert {r["id"] for r in client.get(path, headers=mine).json()} == {private_id, public, shared}
    assert client.get(f"{path}/{shared}/items", headers=outsider).status_code == 404
    assert client.get(f"{path}/{private_id}/items", headers=joined).status_code == 404
    assert client.patch(f"/fridges/{public}", headers=mine, json={"visibility": "private"}).status_code == 200
    assert client.get(f"{path}/{public}/items", headers=outsider).status_code == 404
