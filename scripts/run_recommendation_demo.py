"""Isolated local recommendation demo. Never uses the configured deployment DB."""
import os
import secrets
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEMO=ROOT/'artifacts/local-recommendation-demo'


def main():
    os.chdir(ROOT);DEMO.mkdir(parents=True,exist_ok=True)
    secret=DEMO/'jwt-secret.txt'
    if not secret.exists():secret.write_text(secrets.token_urlsafe(48));secret.chmod(0o600)
    os.environ.update({'DATABASE_URL':'sqlite:///'+str(DEMO/'demo.sqlite3'),'APP_ENV':'development',
        'JWT_SECRET':secret.read_text().strip(),'AUTO_CREATE_TABLES':'false','OCR_ENGINE':'none',
        'OCR_WARMUP':'false','OCR_FAIL_CLOSED':'false','EMAIL_BACKEND':'console',
        'RECIPE_DATA_DIR':str(ROOT/'recipe_data/image-seed'),
        'RECOMMENDATION_MODEL_DIR':str(ROOT/'artifacts/recommendation-full-demo'),
        'UPLOAD_DIR':str(DEMO/'uploads'),'CORS_ORIGINS':'*','TRUSTED_HOSTS':'localhost,127.0.0.1',
        'PUSH_EXPO_ENABLED':'false','LINK_PREVIEW_ENABLED':'false','RATE_LIMIT_ENABLED':'false'})
    # Recover only this isolated demo's old 0016 event revision after main introduced 0016..0018.
    import sqlite3
    database=DEMO/'demo.sqlite3'
    if database.exists():
        with sqlite3.connect(database) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            revision=db.execute('SELECT version_num FROM alembic_version').fetchone() if 'alembic_version' in tables else None
            columns={r[1] for r in db.execute('PRAGMA table_info(users)')}
            if revision==('0016',) and 'recipe_events' in tables and 'household_size' not in columns and 'name_corrections' not in tables:
                with sqlite3.connect(DEMO/'before-merge-migrations.sqlite3') as backup:db.backup(backup)
                db.execute("UPDATE alembic_version SET version_num='0015'")
    subprocess.run([sys.executable,'-m','alembic','upgrade','head'],check=True)
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models import User,Fridge,FridgeMember,FoodItem
    from app.security import hash_password
    from app.timeutil import today
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.username=='localdemo'))
        if user is None:
            user=User(username='localdemo',email='localdemo@example.com',nickname='로컬 시연',password_hash=hash_password('FoodiaryDemo2026!'))
            db.add(user);db.flush()
            fridge=Fridge(name='로컬 시연 냉장고',owner_id=user.id);db.add(fridge);db.flush()
            db.add(FridgeMember(fridge_id=fridge.id,user_id=user.id,role='owner'))
            for name,days in [('계란',2),('양파',6),('당근',12)]:
                db.add(FoodItem(fridge_id=fridge.id,name=name,category='채소' if name!='계란' else '기타',storage='fridge',quantity=2,remaining_quantity=2,unit='개',purchased_on=today(),expiry_date=today()+timedelta(days=days),status='active',added_by=user.id))
            db.commit()
    print('Local demo: http://127.0.0.1:8001 / isolated SQLite / user localdemo',flush=True)
    import uvicorn
    uvicorn.run('app.main:app',host='127.0.0.1',port=8001,log_level='info')

if __name__=='__main__':main()
