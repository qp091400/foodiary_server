"""Re-screen every stored recipe without fetching or destroying the source data."""
import argparse
import csv
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from app.config import settings
from app.services.recipe_quality import QUALITY_VERSION, screen
from app.services.recipe_store import connect, store_path


def run(apply=False):
    root = Path(settings.recipe_data_dir)
    overrides_path = root / 'quality_overrides.json'
    overrides = json.loads(overrides_path.read_text(encoding='utf-8')) if overrides_path.exists() else {}
    with closing(connect()) as source:
        fd, temporary = tempfile.mkstemp(dir=root, suffix='.sqlite3')
        os.close(fd)
        try:
            with closing(sqlite3.connect(temporary)) as db:
                source.backup(db)
                excluded = screen(db, overrides)
                data = json.loads(db.execute('SELECT value FROM metadata').fetchone()[0])
                data.update(quality_version=QUALITY_VERSION, quality_excluded=excluded,
                            source_total=db.execute('SELECT COUNT(*) FROM recipes').fetchone()[0],
                            catalogue_count=db.execute('SELECT COUNT(*) FROM recipes WHERE recommendable=1').fetchone()[0],
                            sources=dict(db.execute('SELECT source,COUNT(*) FROM recipes WHERE recommendable=1 GROUP BY source')),
                            recipes_with_steps=db.execute('SELECT COUNT(*) FROM recipes WHERE recommendable=1 AND has_steps=1').fetchone()[0])
                db.execute('UPDATE metadata SET value=?', (json.dumps(data, ensure_ascii=False),))
                db.commit()
                report = root / f'quality-review-v{QUALITY_VERSION}.csv'
                with report.open('w', encoding='utf-8-sig', newline='') as output:
                    writer = csv.writer(output)
                    writer.writerow(['id', '추천대상', '제외이유', '요리제목', '요리명', '출처', '원문'])
                    for rid, eligible, reason, payload in db.execute('SELECT id,recommendable,exclusion_reason,payload FROM recipes'):
                        r = json.loads(payload)
                        writer.writerow([rid, eligible, reason, r['name'], r.get('dish_name', ''), r['source'], r['source_url']])
                samples = {}
                for reason in excluded:
                    samples[reason] = [json.loads(r[0])['name'] for r in db.execute(
                        'SELECT payload FROM recipes WHERE exclusion_reason=? ORDER BY RANDOM() LIMIT 12', (reason,))]
            # Close the source snapshot before replacement (including Windows).
            source.close()
            if apply:
                os.replace(temporary, store_path())
            return {'applied': apply, 'total': data['source_total'], 'recommended': data['catalogue_count'],
                    'excluded': excluded, 'samples': samples}
        finally:
            Path(temporary).unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.apply), ensure_ascii=False, indent=2))
