"""Read-only indexed catalogue; each request opens the current atomic snapshot."""
import json
import hashlib
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path

from app.config import settings
from app.services.recipe_names import with_display_name
from app.services.recipes import PARENTS, STAPLES, catalogue, ingredient_key, presentable, rank


def store_path():
    return Path(settings.recipe_data_dir) / 'recipes.sqlite3'


def connect():
    return sqlite3.connect(store_path().resolve().as_uri() + '?mode=ro', uri=True)


def recommendations(foods, offset=0, seed=None, limit=30):
    seed = secrets.randbits(31) if seed is None else seed
    def page(rows, metadata, has_more=None):
        return dict(metadata, recipes=rows[:limit], seed=seed,
                    next_offset=offset + limit if (len(rows) > limit if has_more is None else has_more) else None)
    if not store_path().exists():
        data = catalogue()
        rows = rank(data['recipes'], foods, limit=offset + limit + 1, seed=seed)[offset:]
        return page(rows, dict(catalogue_count=len(data['recipes']), source_total=data['source_total'],
                              sample=data['sample'], imported_at=data['imported_at']))
    with closing(connect()) as db:
        # A stable session shuffle makes page boundaries repeatable, including the
        # chosen variant of each dish. A new selection gets a fresh seed.
        db.create_function('recipe_order', 1, lambda rid: hashlib.sha256(f'{seed}:{rid}'.encode()).hexdigest(), deterministic=True)
        metadata = json.loads(db.execute('SELECT value FROM metadata').fetchone()[0])
        required = []
        for food in foods:
            key = ingredient_key(food.name)
            required.append({key, PARENTS[key]} if key in PARENTS else {key})
        rows = []
        available = set().union(*required) if required else set()
        main = available - STAPLES
        if main:
            clauses, params = [], []
            # Older snapshots remain usable until the first screened import is complete.
            columns = {r[1] for r in db.execute('PRAGMA table_info(recipes)')}
            if 'recommendable' in columns:
                clauses.append('r.recommendable=1')
            if 'combination_count' in columns:
                clauses.append('r.combination_count>=2')
            for options in required:
                marks = ','.join('?' for _ in options)
                clauses.append(f'r.id IN (SELECT recipe_id FROM ingredients WHERE key IN ({marks}))')
                params.extend(sorted(options))
            marks = ','.join('?' for _ in main)
            clauses.append(f'r.id IN (SELECT recipe_id FROM ingredients WHERE key IN ({marks}))')
            params.extend(sorted(main))
            available_marks = ','.join('?' for _ in available)
            family = 'r.dish_family' if 'dish_family' in columns else 'r.id'
            # Fewest missing ingredients first; among equals, recipes the app can show step by step
            # (public sources) come before link-only ones.
            no_steps = '(r.has_steps=0)' if 'has_steps' in columns else '0'
            query = (f'WITH scored AS (SELECT r.id, r.payload, {family} AS family, {no_steps} AS no_steps, '
                     f'r.ingredient_count - (SELECT COUNT(*) FROM ingredients i '
                     f'WHERE i.recipe_id=r.id AND i.key IN ({available_marks})) AS missing '
                     'FROM recipes r WHERE ' + ' AND '.join(clauses) + '), '
                     'variants AS (SELECT id,payload,missing,no_steps,ROW_NUMBER() OVER '
                     '(PARTITION BY family ORDER BY missing,no_steps,recipe_order(id),id) AS choice FROM scored) '
                     'SELECT payload FROM variants WHERE choice=1 ORDER BY missing,no_steps,recipe_order(id),id LIMIT ? OFFSET ?')
            rows = [json.loads(row[0]) for row in db.execute(query, [*sorted(available), *params, limit + 1, offset])]
        # Reuse the same strict all-selected rule and response contract as the legacy catalogue.
        return page(rank(rows[:limit], foods, limit=limit, seed=seed), metadata, len(rows) > limit)


def detail(recipe_id):
    if not store_path().exists():
        return presentable(with_display_name(next((r for r in catalogue()['recipes'] if r['id'] == recipe_id), None)))
    with closing(connect()) as db:
        row = db.execute('SELECT payload FROM recipes WHERE id=?', (recipe_id,)).fetchone()
        return presentable(with_display_name(json.loads(row[0]))) if row else None
