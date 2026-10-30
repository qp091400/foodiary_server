"""커뮤니티 2차: 글에 사진 붙이기."""

import io
from pathlib import Path

import pytest
from PIL import Image

from app.config import settings
from app.database import SessionLocal
from app.models import PostPhoto
from app.routers import community
from scripts.set_admin import set_admin
from tests.helpers import auth, image_bytes, signup


@pytest.fixture
def users(client):
    result = {}
    for name in ("minsu", "jiyeon", "boss"):
        tokens = signup(client, name)
        result[name] = {"headers": auth(tokens), "id": tokens["user"]["id"]}
    with SessionLocal() as db:
        set_admin(db, "boss@example.com", True)
    return result


def write(client, who):
    return client.post("/posts", headers=who["headers"], json={"title": "사진 글", "body": "사진을 붙여요"}).json()["id"]


def upload(client, who, post_id, *pictures):
    files = [("files", picture) for picture in pictures]
    return client.post(f"/posts/{post_id}/photos", headers=who["headers"], files=files)


def jpeg(name="a.jpg", size=(300, 200)):
    return (name, image_bytes("JPEG", size), "image/jpeg")


def stored(post_id):
    return sorted((Path(settings.upload_dir) / "posts" / str(post_id)).glob("*.jpg"))


def test_attach_and_download_photos(client, users):
    post_id = write(client, users["minsu"])
    response = upload(client, users["minsu"], post_id, jpeg("a.jpg"), jpeg("b.jpg", (100, 400)))
    assert response.status_code == 201
    photos = response.json()["photos"]
    assert len(photos) == 2 and photos[0]["url"] == f"/posts/{post_id}/photos/{photos[0]['id']}"
    assert len(stored(post_id)) == 2
    for photo in photos:  # 다른 사람도 받을 수 있다 (전체 공개)
        got = client.get(photo["url"], headers=users["jiyeon"]["headers"])
        assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg" and "private" in got.headers["cache-control"]
        assert Image.open(io.BytesIO(got.content)).format == "JPEG"
    assert [p["id"] for p in client.get(f"/posts/{post_id}", headers=users["jiyeon"]["headers"]).json()["photos"]] == [p["id"] for p in photos]
    assert [len(p["photos"]) for p in client.get("/posts", headers=users["jiyeon"]["headers"]).json()["posts"]] == [2]
    assert client.get(photos[0]["url"]).status_code in (401, 403)  # 로그인은 해야 한다


def test_png_and_webp_are_accepted_and_stored_as_jpeg_without_exif(client, users):
    post_id = write(client, users["minsu"])
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"  # 촬영 기기. 위치 같은 개인정보가 사진에 실려 올 수 있다
    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buffer, "JPEG", exif=exif)
    assert len(Image.open(io.BytesIO(buffer.getvalue())).getexif()) > 0
    response = upload(client, users["minsu"], post_id, ("exif.jpg", buffer.getvalue(), "image/jpeg"),
                      ("p.png", image_bytes("PNG", (50, 50)), "image/png"), ("w.webp", image_bytes("WEBP", (50, 50)), "image/webp"))
    assert response.status_code == 201 and len(response.json()["photos"]) == 3
    for photo in response.json()["photos"]:
        picture = Image.open(io.BytesIO(client.get(photo["url"], headers=users["minsu"]["headers"]).content))
        assert picture.format == "JPEG" and len(picture.getexif()) == 0


def test_big_photos_are_shrunk(client, users):
    post_id = write(client, users["minsu"])
    photo = upload(client, users["minsu"], post_id, jpeg("big.jpg", (3000, 1500))).json()["photos"][0]
    assert max(Image.open(io.BytesIO(client.get(photo["url"], headers=users["minsu"]["headers"]).content)).size) <= 2000


def test_only_four_photos_per_post_and_nothing_is_saved_when_it_is_too_many(client, users, monkeypatch):
    post_id = write(client, users["minsu"])
    assert upload(client, users["minsu"], post_id, *[jpeg(f"{n}.jpg") for n in range(3)]).status_code == 201
    too_many = upload(client, users["minsu"], post_id, jpeg("x.jpg"), jpeg("y.jpg"))
    assert too_many.status_code == 400 and str(community.MAX_PHOTOS_PER_POST) in too_many.json()["detail"]
    assert len(stored(post_id)) == 3
    assert upload(client, users["minsu"], post_id, jpeg("z.jpg")).status_code == 201  # 4장째는 된다
    assert upload(client, users["minsu"], post_id, jpeg("w.jpg")).status_code == 400


def test_one_bad_file_means_nothing_is_attached(client, users):
    post_id = write(client, users["minsu"])
    response = upload(client, users["minsu"], post_id, jpeg("ok.jpg"), ("fake.jpg", b"not an image", "image/jpeg"))
    assert response.status_code == 400
    assert upload(client, users["minsu"], post_id, ("note.txt", b"hello", "text/plain")).status_code == 400
    assert upload(client, users["minsu"], post_id, ("empty.jpg", b"", "image/jpeg")).status_code == 400
    assert stored(post_id) == []
    assert client.get(f"/posts/{post_id}", headers=users["minsu"]["headers"]).json()["photos"] == []


def test_only_the_author_attaches_photos(client, users):
    post_id = write(client, users["minsu"])
    assert upload(client, users["jiyeon"], post_id, jpeg()).status_code == 403
    assert upload(client, users["boss"], post_id, jpeg()).status_code == 403  # 관리자도 남의 글에 붙이지는 못한다
    assert upload(client, users["minsu"], 99999, jpeg()).status_code == 404
    assert stored(post_id) == []


def test_remove_a_photo(client, users):
    post_id = write(client, users["minsu"])
    first, second = upload(client, users["minsu"], post_id, jpeg("a.jpg"), jpeg("b.jpg")).json()["photos"]
    assert client.delete(f"/posts/{post_id}/photos/{first['id']}", headers=users["jiyeon"]["headers"]).status_code == 403
    assert client.delete(f"/posts/{post_id}/photos/{first['id']}", headers=users["minsu"]["headers"]).status_code == 204
    assert client.get(first["url"], headers=users["minsu"]["headers"]).status_code == 404
    assert len(stored(post_id)) == 1
    assert client.delete(f"/posts/{post_id}/photos/{second['id']}", headers=users["boss"]["headers"]).status_code == 204  # 관리자
    assert stored(post_id) == []
    assert client.delete(f"/posts/{post_id}/photos/{second['id']}", headers=users["boss"]["headers"]).status_code == 404


def test_deleting_the_post_deletes_its_photos(client, users):
    post_id = write(client, users["minsu"])
    photo = upload(client, users["minsu"], post_id, jpeg(), jpeg("b.jpg")).json()["photos"][0]
    assert client.delete(f"/posts/{post_id}", headers=users["minsu"]["headers"]).status_code == 204
    assert stored(post_id) == []
    assert client.get(photo["url"], headers=users["minsu"]["headers"]).status_code == 404
    with SessionLocal() as db:
        assert db.query(PostPhoto).filter_by(post_id=post_id).count() == 0


def test_a_photo_is_only_reachable_through_its_own_post(client, users):
    first, second = write(client, users["minsu"]), write(client, users["minsu"])
    photo = upload(client, users["minsu"], first, jpeg()).json()["photos"][0]
    assert client.get(f"/posts/{second}/photos/{photo['id']}", headers=users["minsu"]["headers"]).status_code == 404
    (Path(settings.upload_dir) / "posts" / str(first) / next(iter(stored(first))).name).unlink()  # 파일이 사라졌다면
    assert client.get(photo["url"], headers=users["minsu"]["headers"]).status_code == 404
