"""API 문서 만들기: 모든 경로가 들어가고, 외부 파일 없이 열리고, 명세의 글이 그대로 HTML이 되지 않는다."""

import html
import json
import re

from app.main import app
from scripts import export_api_docs
from scripts.export_api_docs import field_rows, render, resolve, type_label

METHODS = export_api_docs.METHODS


def test_every_route_of_the_app_is_in_the_page_and_the_json(tmp_path):
    export_api_docs.main(["--out", str(tmp_path)])
    spec = json.loads((tmp_path / "openapi.json").read_text(encoding="utf-8"))
    page = (tmp_path / "api.html").read_text(encoding="utf-8")
    assert spec == json.loads(json.dumps(app.openapi()))  # 서버가 실제로 내는 명세와 같다
    operations = [(method, path) for path, item in spec["paths"].items() for method in METHODS if method in item]
    assert len(operations) > 70 and page.count('class="op"') == len(operations)  # 경로가 통째로 빠지지 않았는지 보는 하한선 (친구 기능을 지워 76개)
    for method, path in operations:
        assert f'<code class="path">{html.escape(path)}</code>' in page, (method, path)
    for needed in ("/notifications/summary", "/notifications/expiring", "/share-links/{token}/join", "/fridges/{fridge_id}/share-link/qr", "/recipes/{recipe_id}/reviews/mine"):
        assert any(path.startswith(needed) for _, path in operations), needed
    assert "공유 링크 QR 이미지" in page and "로그인 필요" in page


def test_the_page_opens_by_itself_without_any_outside_file(tmp_path):
    export_api_docs.main(["--out", str(tmp_path)])
    page = (tmp_path / "api.html").read_text(encoding="utf-8")
    assert not re.search(r"<script[^>]+src=|<link[^>]+href=|<img[^>]+src=|@import|url\(http", page)
    assert '<meta name="robots" content="noindex">' in page and '<html lang="ko">' in page


def test_text_from_the_spec_is_escaped_so_a_summary_cannot_inject_markup():
    spec = {
        "info": {"title": "<b>제목</b>", "version": "1", "description": "<script>alert(1)</script>"},
        "paths": {"/x/{id}": {"get": {"tags": ["<i>분류</i>"], "summary": "<script>alert('요약')</script>", "responses": {"200": {"description": "<u>ok</u>"}}}}},
    }
    page = render(spec)
    assert "<script>alert" not in page.replace("<script>" + export_api_docs.JS, "")  # 우리가 넣은 스크립트 하나만 있다
    assert page.count("<script") == 1
    assert "&lt;script&gt;alert(&#x27;요약&#x27;)&lt;/script&gt;" in page and "&lt;b&gt;제목&lt;/b&gt;" in page and "&lt;u&gt;ok&lt;/u&gt;" in page


def test_nested_objects_are_flattened_and_a_self_referencing_schema_stops():
    spec = {
        "components": {
            "schemas": {
                "Node": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}, "child": {"$ref": "#/components/schemas/Node"}, "tags": {"type": "array", "items": {"type": "string"}}}},
                "Out": {
                    "type": "object",
                    "required": ["mine"],
                    "properties": {
                        "mine": {"anyOf": [{"$ref": "#/components/schemas/Node"}, {"type": "null"}], "description": "내 것"},
                        "rows": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}},
                        "stars": {"enum": [1, 2, 3, 4, 5], "type": "integer"},
                    },
                },
            }
        }
    }
    rows = {name: (kind, required, text) for name, kind, required, text in field_rows(spec, {"$ref": "#/components/schemas/Out"})}
    assert rows["mine"] == ("Node (비울 수 있음)", True, "내 것")
    assert rows["mine.id"] == ("integer", True, "") and rows["rows[].id"][0] == "integer"
    assert rows["mine.tags"][0] == "목록[string]" and rows["stars"][0] == "1 / 2 / 3 / 4 / 5"
    assert "mine.child.id" not in rows  # 같은 객체가 되풀이되면 더 펼치지 않는다
    assert len(rows) < 20  # 끝없이 펼쳐지지 않는다


def test_reference_and_type_helpers():
    spec = {"components": {"schemas": {"A": {"type": "object", "properties": {"x": {"type": "string"}}}}}}
    assert resolve(spec, {"$ref": "#/components/schemas/A"})["type"] == "object"
    assert type_label(spec, {"$ref": "#/components/schemas/A"}) == "A"
    assert type_label(spec, {"type": "string", "format": "date"}) == "string (date)"
    assert type_label(spec, {"type": "array", "items": {"type": "integer"}}) == "목록[integer]"
