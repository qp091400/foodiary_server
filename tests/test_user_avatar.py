import io
from pathlib import Path
from PIL import Image
from app.config import settings
from tests.helpers import signup, auth, image_bytes


def test_avatar_upload_account_and_community(client):
    first, other = signup(client), signup(client, "other")
    headers = auth(first)
    r = client.post("/users/me/avatar", headers=headers, files={"file": ("profile.png", image_bytes("PNG", (900, 600)), "image/png")})
    assert r.status_code == 200, r.text
    url = r.json()["avatar_url"]
    assert url.startswith(f"/users/{first['user']['id']}/avatar/")
    assert client.get("/users/me", headers=headers).json()["avatar_url"] == url
    assert client.get(url).status_code == 401
    got = client.get(url, headers=auth(other))
    assert got.status_code == 200
    image = Image.open(io.BytesIO(got.content))
    assert image.size == (512, 512) and image.format == "JPEG" and not image.getexif()
    post = client.post("/posts", headers=headers, json={"title": "Test", "body": "Profile"}).json()
    assert post["author"]["avatar_url"] == url
    comment = client.post(f"/posts/{post['id']}/comments", headers=headers, json={"body": "Hi"}).json()
    assert comment["author"]["avatar_url"] == url


def test_replace_delete_and_invalid_upload_preserve_previous(client):
    user = signup(client)
    headers = auth(user)
    def upload(data):
        return client.post("/users/me/avatar", headers=headers, files={"file": ("a.jpg", data, "image/jpeg")})
    first = upload(image_bytes()).json()["avatar_url"]
    assert upload(b"not an image").status_code == 400
    assert client.get("/users/me", headers=headers).json()["avatar_url"] == first
    second = upload(image_bytes()).json()["avatar_url"]
    assert second != first
    assert client.get(first, headers=headers).status_code == 404
    assert len(list((Path(settings.upload_dir) / "avatars" / str(user["user"]["id"])).glob("*.jpg"))) == 1
    assert client.delete("/users/me/avatar", headers=headers).status_code == 204
    assert client.delete("/users/me/avatar", headers=headers).status_code == 204
    assert client.get(second, headers=headers).status_code == 404
    assert client.get("/users/me", headers=headers).json()["avatar_url"] is None
    assert client.post("/users/me/avatar", files={"file": ("a.jpg", image_bytes(), "image/jpeg")}).status_code == 401
