"""Fail-closed local activation after independent study gates and runtime smoke checks."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('study',type=Path);p.add_argument('bundle',type=Path)
    p.add_argument('--catalogue',type=Path,default=Path('recipe_data/image-seed/recipes.sqlite3'))
    p.add_argument('--app-inference',type=Path,default=Path('../foodiary_app/src/lib/recommendation/inference.ts'))
    p.add_argument('--demo',action='store_true',help='User-authorized local demo; preserves failed research gates in report')
    p.add_argument('--node',default='node');a=p.parse_args()
    comparison=json.loads((a.study/'comparison.json').read_text())
    if a.demo:
        policy=json.loads((a.study/'demo-policy.json').read_text())
        candidate=comparison['common_test'][comparison['selected_candidate']]
        baseline=comparison['common_test']['v1_sample']
        if policy['selected_candidate']!=comparison['selected_candidate'] or candidate['eligible_users']<30 or candidate['ndcg_at_10']<=baseline['ndcg_at_10'] or not comparison['gates']['korean_test_retrieval_improved']:
            raise ValueError('Demo recommendation/semantic checks did not pass')
    elif not comparison['promote'] or not all(comparison['gates'].values()):
        raise ValueError('Measured promotion gates did not pass; leave incumbent active')
    name=comparison['selected_candidate'];run=a.study/name
    model=json.loads((run/'ranking.json').read_text())
    if model['version']!=comparison['common_test'][name]['version'] or hashlib.sha256((run/'ranking.json').read_bytes()).hexdigest()!=comparison['common_test'][name]['ranking_sha256']:
        raise ValueError('Evaluated model changed')
    subprocess.run([sys.executable,'-m','ml.verify_runtime',str(run),str(a.app_inference),'--node',a.node],check=True)
    subprocess.run([sys.executable,'-m','ml.package_bundle',str(run),str(a.catalogue),str(a.bundle),'--cache',str(a.study/json.loads((run/'report.json').read_text())['representation'].get('cache_name','cache')/'korean')],check=True)
    env={**os.environ,'RECOMMENDATION_TEST_BUNDLE':str(a.bundle.resolve()),'RECOMMENDATION_TEST_CATALOGUE':str(a.catalogue.parent.resolve())}
    subprocess.run([sys.executable,'-m','pytest','tests/test_recommendation_real_bundle.py','-q'],env=env,check=True)
    from app.config import settings
    if settings.app_env!='development':raise ValueError('Automatic activation is restricted to local development')
    path=Path('.env');original=path.read_text() if path.exists() else ''
    lines=[line for line in original.splitlines() if not line.startswith('RECOMMENDATION_MODEL_DIR=')]
    lines.append('RECOMMENDATION_MODEL_DIR='+str(a.bundle.resolve()))
    temporary=path.with_name('.env.recommendation.tmp');temporary.write_text('\n'.join(lines)+'\n');temporary.replace(path)
    (a.study/'activation.json').write_text(json.dumps({'version':model['version'],'bundle':str(a.bundle.resolve()),'scope':'local demonstration .env, effective after API restart' if a.demo else 'local development .env, effective after API restart','demo_mode':a.demo,'research_gates_passed':comparison['promote'],'checks':['statistical gates','TypeScript/PyTorch parity','real-bundle API smoke']},indent=2))
    print('Activated locally:',model['version'])

if __name__=='__main__':main()
