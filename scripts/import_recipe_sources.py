"""Build an atomic indexed catalogue from FoodSafety, MAFRA and user-downloaded CSVs.

python -m scripts.import_recipe_sources --mafra --csv /imports/*.csv
Source files and credentials must stay outside Git and the application bundle.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from app.config import settings
from app.services.recipes import ingredient_parts
from app.services.recipe_quality import QUALITY_VERSION, screen

MAFRA_SOURCE = '농림수산식품교육문화정보원 · 농식품 레시피'
MAFRA_URL = 'https://data.mafra.go.kr/opendata/data/indexOpenDataDetail.do?data_id=20150827000000000464'
GRIDS = {name: f'Grid_20150827000000000{number}_1'
         for name, number in [('basic', 226), ('ingredients', 227), ('steps', 228)]}


def fetch_mafra(grid, start, end):
    key = settings.mafra_api_key.strip()
    if not key:
        raise RuntimeError('MAFRA_API_KEY를 서버 .env에 설정해주세요.')
    url = f'{settings.mafra_api_base}/{key}/json/{grid}/{start}/{end}'
    try:
        with urlopen(url, timeout=45) as response:
            result = json.load(response)
        data = result.get(grid, {})
        if data.get('result', {}).get('code') != 'INFO-000':
            raise ValueError('response')
        return data
    except Exception:
        # URLs contain credentials. Never include the original exception in logs.
        raise RuntimeError('농식품 API를 받지 못했습니다. 인증키·신청 IP·호출 한도를 확인해주세요.') from None


def collect_mafra():
    collected = {}
    for name, grid in GRIDS.items():
        first = fetch_mafra(grid, 1, 1000)
        total = int(first['totalCnt'])
        rows = list(first['row'])
        for start in range(1001, total + 1, 1000):
            page = fetch_mafra(grid, start, min(start + 999, total))
            if int(page['totalCnt']) != total:
                raise RuntimeError('농식품 원본 건수가 수집 중 바뀌었습니다.')
            rows.extend(page['row'])
        if len(rows) != total or len({r['ROW_NUM'] for r in rows}) != total:
            raise RuntimeError('농식품 API 일부 페이지가 누락됐습니다.')
        collected[name] = rows
    return collected


def mafra_recipes(data):
    ingredients, steps = defaultdict(list), defaultdict(list)
    for row in data['ingredients']:
        ingredients[str(row['RECIPE_ID'])].append(row)
    for row in data['steps']:
        steps[str(row['RECIPE_ID'])].append(row)
    for row in data['basic']:
        rid = str(row['RECIPE_ID'])
        raw = '\n'.join(f"{r['IRDNT_NM']} {r.get('IRDNT_CPCTY', '')}" for r in
                        sorted(ingredients[rid], key=lambda r: int(r['IRDNT_SN'])))
        ordered = sorted(steps[rid], key=lambda r: int(r['COOKING_NO']))
        yield dict(id='mafra:' + rid, name=row['RECIPE_NM_KO'], category=row.get('TY_NM', ''),
                   method='', ingredients=ingredient_parts(raw), ingredients_text=raw,
                   steps=[r['COOKING_DC'] for r in ordered if r.get('COOKING_DC', '').strip()],
                   tip='\n'.join(r['STEP_TIP'] for r in ordered if r.get('STEP_TIP')),
                   description=row.get('SUMRY', ''), cooking_time=row.get('COOKING_TIME', ''),
                   difficulty=row.get('LEVEL_NM', ''), servings=row.get('QNT', ''),
                   source=MAFRA_SOURCE, source_url=MAFRA_URL, source_key='mafra',
                   license_note='제공기관의 현행 이용조건 및 출처표시를 따릅니다.')


def csv_recipes(path):
    raw = path.read_bytes()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp949', errors='replace')
    reader = csv.DictReader(io.StringIO(text, newline=''))
    needed = {'RCP_SNO', 'RCP_TTL', 'CKG_MTRL_CN'}
    if not needed.issubset(reader.fieldnames or []):
        raise RuntimeError(f'{path.name}: 레시피 필수 열이 없습니다.')
    for row in reader:
        rid = (row.get('RCP_SNO') or '').strip()
        name = (row.get('RCP_TTL') or row.get('CKG_NM') or '').strip()
        raw_parts = (row.get('CKG_MTRL_CN') or '').replace('\x07', ' ')
        # Keep malformed records out of matching instead of silently guessing a name.
        if not rid.isdigit() or not name or '\ufffd' in name + raw_parts:
            continue
        raw_parts = re.sub(r'[ \t]+', ' ', raw_parts)
        yield dict(id='10000recipe:' + rid, name=name, category=row.get('CKG_KND_ACTO_NM', ''),
                   dish_name=row.get('CKG_NM', ''),
                   method=row.get('CKG_MTH_ACTO_NM', ''), ingredients=ingredient_parts(raw_parts),
                   ingredients_text=raw_parts.replace('|', '\n'), steps=[], tip='',
                   description=row.get('CKG_IPDC', ''), cooking_time=row.get('CKG_TIME_NM', ''),
                   difficulty=row.get('CKG_DODF_NM', ''), servings=row.get('CKG_INBUN_NM', ''),
                   source='만개의레시피 · KADX 제공 CSV', source_key='10000recipe',
                   source_url=f'https://www.10000recipe.com/recipe/{rid}',
                   license_note='KADX에서 받은 파일의 이용조건 적용. 조리 순서·사진은 수집하지 않습니다.')


def build_store(destination, sources, manifest, overrides=None):
    """Construct separately; a failed import never replaces the working snapshot."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=destination.parent, suffix='.sqlite3')
    os.close(fd)
    try:
        with closing(sqlite3.connect(temporary)) as db:
            db.executescript('''
                CREATE TABLE recipes (id TEXT PRIMARY KEY, source TEXT, ingredient_count INTEGER,
                                      has_steps INTEGER, payload TEXT NOT NULL,
                                      recommendable INTEGER DEFAULT 1, exclusion_reason TEXT DEFAULT '', group_key TEXT);
                CREATE TABLE ingredients (recipe_id TEXT, key TEXT, PRIMARY KEY(recipe_id,key));
                CREATE TABLE metadata (value TEXT NOT NULL);
            ''')
            for source in sources:
                for recipe in source:
                    if not recipe.get('ingredients'):
                        continue
                    rid = recipe['id']
                    parts = {part['key'] for part in recipe['ingredients']}
                    db.execute('INSERT OR REPLACE INTO recipes(id,source,ingredient_count,has_steps,payload) VALUES (?,?,?,?,?)',
                               (rid, recipe.get('source_key', 'foodsafety'), len(parts),
                                bool(recipe['steps']), json.dumps(recipe, ensure_ascii=False)))
                    db.execute('DELETE FROM ingredients WHERE recipe_id=?', (rid,))
                    db.executemany('INSERT INTO ingredients VALUES (?,?)', [(rid, k) for k in parts])
            db.execute('CREATE INDEX ingredient_lookup ON ingredients(key,recipe_id)')
            excluded = screen(db, overrides)
            counts = {r[0]: r[1] for r in db.execute('SELECT source,COUNT(*) FROM recipes WHERE recommendable=1 GROUP BY source')}
            total = sum(counts.values())
            if not total:
                raise RuntimeError('사용 가능한 레시피가 없습니다. 기존 자료를 유지합니다.')
            metadata = dict(catalogue_count=total, source_total=db.execute('SELECT COUNT(*) FROM recipes').fetchone()[0], sample=False,
                            imported_at=datetime.now(timezone.utc).isoformat(), sources=counts,
                            recipes_with_steps=db.execute('SELECT COUNT(*) FROM recipes WHERE has_steps=1 AND recommendable=1').fetchone()[0],
                            quality_excluded=excluded,
                            quality_version=QUALITY_VERSION,
                            manifest=manifest)
            db.execute('INSERT INTO metadata VALUES (?)', (json.dumps(metadata, ensure_ascii=False),))
            db.execute('ANALYZE')
            db.commit()
        os.replace(temporary, destination)
        return metadata
    finally:
        Path(temporary).unlink(missing_ok=True)


def run(csv_paths, mafra=False):
    root = Path(settings.recipe_data_dir)
    root.mkdir(parents=True, exist_ok=True)
    existing = root / 'recipes.sqlite3'
    if existing.exists() and not csv_paths:
        with sqlite3.connect(existing) as db:
            if db.execute("SELECT 1 FROM recipes WHERE source='10000recipe' LIMIT 1").fetchone():
                raise RuntimeError('기존 CSV 자료를 유지하려면 --csv에 전체 파일 목록을 전달해주세요.')
    sources, manifest = [], []
    legacy = root / 'catalogue.json'
    if legacy.exists():
        data = json.loads(legacy.read_text(encoding='utf-8'))
        if data.get('sample'):
            raise RuntimeError('공식 샘플 대신 식약처 전체 자료를 먼저 가져와 주세요.')
        sources.append(data['recipes'])
        manifest.append({'source': 'foodsafety', 'sha256': hashlib.sha256(legacy.read_bytes()).hexdigest()})
    mafra_cache = root / 'mafra-source.json'
    if mafra:
        data = collect_mafra()
        temp = mafra_cache.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, mafra_cache)
    if mafra_cache.exists():
        sources.append(mafra_recipes(json.loads(mafra_cache.read_text(encoding='utf-8'))))
        manifest.append({'source': 'mafra', 'sha256': hashlib.sha256(mafra_cache.read_bytes()).hexdigest()})
    # Old snapshots first, newer snapshots replace matching source IDs.
    def snapshot_order(path):
        match = re.search(r'(\d{6})\.csv$', path.name)
        return match.group(1) if match else path.name
    paths = sorted(map(Path, csv_paths), key=snapshot_order)
    for path in paths:
        sources.append(csv_recipes(path))
        manifest.append({'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    overrides_path = root / 'quality_overrides.json'
    overrides = json.loads(overrides_path.read_text(encoding='utf-8')) if overrides_path.exists() else {}
    return build_store(root / 'recipes.sqlite3', sources, manifest, overrides)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', nargs='*', default=[])
    parser.add_argument('--mafra', action='store_true')
    args = parser.parse_args()
    try:
        result = run(args.csv, args.mafra)
        print(json.dumps({k: result[k] for k in ('source_total', 'catalogue_count', 'sources', 'recipes_with_steps', 'quality_excluded')}, ensure_ascii=False))
    except RuntimeError as error:
        parser.exit(1, str(error) + '\n')
