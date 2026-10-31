"""API 문서를 코드에서 만든다. 운영 서버의 /docs는 꺼 두었으므로, 이 파일들로 API를 본다.

    python -m scripts.export_api_docs              # docs/openapi.json 과 docs/api.html
    python -m scripts.export_api_docs --out 폴더   # 다른 폴더에 만든다

api.html은 외부 파일 없이 브라우저로 바로 열린다. 서버에는 아무것도 올리지 않고, DB에도 접속하지 않는다.
API를 고친 뒤에는 이 명령으로 다시 만든다.
"""

import argparse
import html
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METHODS = ("get", "post", "put", "patch", "delete")
DEFAULT_TEXT = {"Successful Response": "성공", "Validation Error": "입력값 오류", "Not Found": "찾을 수 없음"}  # FastAPI가 붙이는 영어 기본 문구
MAX_DEPTH = 2  # 응답·요청의 안쪽 객체를 몇 단계까지 펼쳐 보일지


def load_schema() -> dict:
    os.environ.setdefault("DATABASE_URL", "sqlite://")  # 문서만 만들 뿐 DB에는 붙지 않는다
    from app.main import app

    return app.openapi()


# ── OpenAPI 읽기 ─────────────────────────────────────────────────────────


def resolve(spec: dict, node: dict) -> dict:
    """{"$ref": "#/components/schemas/Name"} 같은 참조를 따라가 실제 내용을 돌려준다."""
    seen = 0
    while isinstance(node, dict) and "$ref" in node and seen < 20:
        target = spec
        for part in node["$ref"][2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        node, seen = target, seen + 1
    return node


def ref_name(node: dict) -> str | None:
    return node["$ref"].rsplit("/", 1)[-1] if "$ref" in node else None


def non_null(spec: dict, options: list[dict]) -> list[dict]:
    return [option for option in options if resolve(spec, option).get("type") != "null"]


def type_label(spec: dict, node: dict) -> str:
    """사람이 읽을 형식 이름. 예: string, integer, 목록[ReviewOut], 1/2/3/4/5."""
    name = ref_name(node)
    schema = resolve(spec, node)
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        kept = non_null(spec, options)
        text = " 또는 ".join(type_label(spec, option) for option in kept) or "null"
        return text + (" (비울 수 있음)" if len(kept) < len(options) and kept else "")
    if "enum" in schema:
        return " / ".join(str(value) for value in schema["enum"])
    if "const" in schema:
        return str(schema["const"])
    kind = schema.get("type")
    if kind == "array":
        return f"목록[{type_label(spec, schema.get('items', {}))}]"
    if kind == "object":
        return name or ("사전" if schema.get("additionalProperties") else "객체")
    if kind and schema.get("format"):
        return f"{kind} ({schema['format']})"
    return kind or name or "any"


def inner_object(spec: dict, node: dict) -> tuple[dict | None, bool]:
    """node가 (비울 수 있는) 객체나 객체 목록이면 그 객체와 목록 여부를 돌려준다. 아니면 (None, False)."""
    schema = resolve(spec, node)
    for key in ("anyOf", "oneOf"):
        kept = non_null(spec, schema.get(key, []))
        if len(kept) == 1:
            return inner_object(spec, kept[0])
    if schema.get("type") == "array":
        obj, _ = inner_object(spec, schema.get("items", {}))
        return (obj, True) if obj else (None, False)
    return (schema, False) if schema.get("properties") else (None, False)


def field_rows(spec: dict, node: dict, prefix: str = "", depth: int = 0, trail: tuple = ()) -> list[tuple[str, str, bool, str]]:
    """객체의 칸들을 (이름, 형식, 필수, 설명)으로 펴 준다. 안쪽 객체는 MAX_DEPTH단계까지, 같은 객체가 되풀이되면 멈춘다."""
    obj, _ = inner_object(spec, node)
    if obj is None:
        return []
    trail += (id(obj),)  # 지금 펼치는 객체. 같은 객체를 그 안에서 또 만나면 더 펼치지 않는다
    required = set(obj.get("required", []))
    rows = []
    for name, prop in obj.get("properties", {}).items():
        described = resolve(spec, prop)
        rows.append((prefix + name, type_label(spec, prop), name in required, prop.get("description") or described.get("description") or ""))
        nested, is_list = inner_object(spec, prop)
        if nested is not None and depth < MAX_DEPTH and id(nested) not in trail:
            rows += field_rows(spec, prop, f"{prefix}{name}{'[]' if is_list else ''}.", depth + 1, trail)
    return rows


# ── HTML ─────────────────────────────────────────────────────────────────


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def table(headers: list[str], rows: list[list[str]]) -> str:
    """셀은 이미 이스케이프된 HTML이다."""
    head = "".join(f"<th>{e(header)}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def field_table(rows: list[tuple[str, str, bool, str]]) -> str:
    return table(
        ["이름", "형식", "필수", "설명"],
        [[f"<code>{e(name)}</code>", e(kind), '<span class="req">필수</span>' if required else "", e(text)] for name, kind, required, text in rows],
    )


def render_params(spec: dict, params: list[dict]) -> str:
    rows = []
    for raw in params:
        param = resolve(spec, raw)
        rows.append(
            [
                f"<code>{e(param.get('name', ''))}</code>",
                e({"path": "경로", "query": "쿼리", "header": "헤더", "cookie": "쿠키"}.get(param.get("in", ""), param.get("in", ""))),
                '<span class="req">필수</span>' if param.get("required") else "",
                e(type_label(spec, param.get("schema", {}))),
                e(param.get("description", "")),
            ]
        )
    return f"<h4>요청 매개변수</h4>{table(['이름', '위치', '필수', '형식', '설명'], rows)}" if rows else ""


def render_body(spec: dict, body: dict | None) -> str:
    if not body:
        return ""
    parts = []
    for content_type, content in resolve(spec, body).get("content", {}).items():
        schema = content.get("schema", {})
        rows = field_rows(spec, schema)
        label = f"요청 본문 <small>{e(content_type)}</small>" + (" (필수)" if body.get("required") else "")
        parts.append(f"<h4>{label}</h4>" + (field_table(rows) if rows else f"<p>{e(type_label(spec, schema))}</p>"))
    return "".join(parts)


def render_responses(spec: dict, responses: dict) -> str:
    codes, detail = [], ""
    for code, response in responses.items():
        response = resolve(spec, response)
        schema = next((c.get("schema") for c in response.get("content", {}).values() if c.get("schema")), None)
        label = type_label(spec, schema) if schema else "본문 없음"
        description = response.get("description", "")
        codes.append([f"<code>{e(code)}</code>", e(DEFAULT_TEXT.get(description, description)), e(label)])
        if schema and not detail and str(code).startswith("2") and (rows := field_rows(spec, schema)):
            detail = f"<h4>성공 응답 {e(code)}의 칸</h4>{field_table(rows)}"
    return f"<h4>응답</h4>{table(['코드', '설명', '형식'], codes)}{detail}"


def render_operation(spec: dict, method: str, path: str, op: dict, shared: list[dict]) -> str:
    summary = op.get("summary", "")
    login = '<span class="lock">로그인 필요</span>' if op.get("security") else ""
    description = f"<p>{e(op['description'])}</p>" if op.get("description") and op["description"] != summary else ""
    body = render_params(spec, shared + op.get("parameters", [])) + render_body(spec, op.get("requestBody")) + render_responses(spec, op.get("responses", {}))
    text = f"{method} {path} {summary}".lower()
    return (
        f'<details class="op" data-text="{e(text)}"><summary>'
        f'<span class="m {e(method)}">{e(method.upper())}</span><code class="path">{e(path)}</code>'
        f'<span class="what">{e(summary)}</span>{login}</summary>{description}{body}</details>'
    )


CSS = """
:root { --bg:#fff; --fg:#1d2330; --muted:#667085; --line:#e4e7ec; --card:#f7f8fa; --accent:#2f6fed; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171f; --fg:#e8eaf0; --muted:#98a2b3; --line:#2a303d; --card:#1b202b; --accent:#7aa2ff; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 system-ui,-apple-system,"Segoe UI","Malgun Gothic",sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 24px 16px 80px; }
h1 { margin:0 0 4px; font-size:24px; } h2 { margin:36px 0 10px; padding-bottom:6px; border-bottom:1px solid var(--line); font-size:19px; }
h4 { margin:16px 0 6px; font-size:13px; color:var(--muted); } small { color:var(--muted); font-weight:400; }
p { margin:6px 0; } .lead { color:var(--muted); }
.tools { position:sticky; top:0; background:var(--bg); padding:10px 0; z-index:1; display:flex; gap:8px; align-items:center; }
.tools input { flex:1; padding:9px 12px; font:inherit; color:var(--fg); background:var(--card); border:1px solid var(--line); border-radius:8px; }
.tools button { padding:9px 12px; font:inherit; color:var(--fg); background:var(--card); border:1px solid var(--line); border-radius:8px; cursor:pointer; }
nav { display:flex; flex-wrap:wrap; gap:6px; margin:10px 0 0; } nav a { padding:3px 10px; border:1px solid var(--line); border-radius:99px; color:var(--accent); text-decoration:none; font-size:13px; }
details.op { margin:6px 0; border:1px solid var(--line); border-radius:10px; background:var(--card); }
details.op > summary { display:flex; flex-wrap:wrap; gap:4px 10px; align-items:center; padding:9px 12px; cursor:pointer; list-style:none; }
details.op > summary::-webkit-details-marker { display:none; } details.op[open] > summary { border-bottom:1px solid var(--line); }
details.op > :not(summary) { margin-left:14px; margin-right:14px; } details.op > :last-child { margin-bottom:14px; }
.m { min-width:58px; padding:2px 8px; border-radius:6px; text-align:center; font-size:12px; font-weight:700; color:#fff; background:#667085; }
.m.get { background:#1f8a4c; } .m.post { background:#2f6fed; } .m.put { background:#b7791f; } .m.patch { background:#8a5cf5; } .m.delete { background:#d64545; }
.path { font-size:14px; word-break:break-all; } .what { color:var(--muted); } .lock { margin-left:auto; padding:1px 8px; border:1px solid var(--line); border-radius:99px; font-size:12px; color:var(--muted); }
table { width:100%; border-collapse:collapse; font-size:13px; } th,td { padding:5px 8px; text-align:left; vertical-align:top; border-bottom:1px solid var(--line); } th { color:var(--muted); font-weight:600; }
.req { white-space:nowrap; }
details.op > table { width:calc(100% - 28px); }
code { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:13px; }
.hidden { display:none; }
"""

JS = """
const box = document.getElementById('q'), ops = [...document.querySelectorAll('details.op')], groups = [...document.querySelectorAll('section.group')];
const count = document.getElementById('count');
function apply() {
  const q = box.value.trim().toLowerCase();
  let shown = 0;
  ops.forEach(o => { const hit = !q || o.dataset.text.includes(q); o.classList.toggle('hidden', !hit); shown += hit; });
  groups.forEach(g => g.classList.toggle('hidden', !g.querySelector('details.op:not(.hidden)')));
  count.textContent = q ? shown + '개 찾음' : '';
}
box.addEventListener('input', apply);
document.getElementById('open').addEventListener('click', () => { const any = ops.some(o => !o.open && !o.classList.contains('hidden')); ops.forEach(o => { if (!o.classList.contains('hidden')) o.open = any; }); });
"""


def render(spec: dict) -> str:
    groups: dict[str, list[str]] = {}
    for path, item in spec.get("paths", {}).items():
        shared = item.get("parameters", [])
        for method in METHODS:
            if op := item.get(method):
                groups.setdefault((op.get("tags") or ["기타"])[0], []).append(render_operation(spec, method, path, op, shared))
    total = sum(len(items) for items in groups.values())
    info = spec.get("info", {})
    names = list(groups)
    nav = "".join(f'<a href="#g{i}">{e(name)} <small>{len(groups[name])}</small></a>' for i, name in enumerate(names))
    sections = "".join(
        f'<section class="group" id="g{i}"><h2>{e(name)} <small>{len(groups[name])}개</small></h2>{"".join(groups[name])}</section>'
        for i, name in enumerate(names)
    )
    return (
        f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="robots" content="noindex"><title>{e(info.get("title", "API"))} 문서</title><style>{CSS}</style></head><body><main>'
        f'<h1>{e(info.get("title", "API"))}</h1>'
        f'<p class="lead">{e(info.get("description", ""))}</p>'
        f'<p class="lead">경로 {total}개 · 버전 {e(info.get("version", ""))} · 코드에서 자동으로 만든 문서다. API를 고친 뒤에는 <code>python -m scripts.export_api_docs</code>로 다시 만든다.</p>'
        f'<p class="lead">"로그인 필요"가 붙은 경로는 <code>Authorization: Bearer 토큰</code> 헤더가 있어야 한다. 시각은 모두 UTC(<code>...Z</code>)로 온다.</p>'
        f'<div class="tools"><input id="q" type="search" placeholder="경로나 설명으로 찾기 (예: 친구, notifications, review)" autocomplete="off">'
        f'<span id="count" class="lead"></span><button id="open" type="button">모두 펼치기/접기</button></div><nav>{nav}</nav>{sections}'
        f'</main><script>{JS}</script></body></html>'
    )


# ── 실행 ─────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "docs", help="파일을 만들 폴더 (기본: docs)")
    args = parser.parse_args(argv)
    spec = load_schema()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "openapi.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (args.out / "api.html").write_text(render(spec), encoding="utf-8", newline="\n")
    paths = sum(1 for item in spec["paths"].values() for method in METHODS if method in item)
    print(f"API {paths}개를 {args.out / 'api.html'} 와 {args.out / 'openapi.json'} 에 만들었습니다.")


if __name__ == "__main__":
    main()
