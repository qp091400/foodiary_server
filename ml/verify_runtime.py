"""Compare exported TypeScript inference against the selected PyTorch checkpoint."""
import argparse
import json
import subprocess
import tempfile
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);p.add_argument('inference',type=Path);p.add_argument('--node',default='node')
    a=p.parse_args()
    import torch
    import numpy as np
    m=torch.nn.Sequential(torch.nn.Linear(2,32),torch.nn.ReLU(),torch.nn.Linear(32,16),torch.nn.ReLU(),torch.nn.Linear(16,1))
    m.load_state_dict(torch.load(a.run/'ranking.pt',weights_only=True));m.eval()
    rng=np.random.default_rng(42)
    inputs=np.column_stack([rng.uniform(-1,1,1000),rng.uniform(0,1,1000)]).astype('float32')
    with tempfile.TemporaryDirectory() as tmp:
        script=Path(tmp)/'check.mjs'
        script.write_text("import {infer} from "+json.dumps(a.inference.resolve().as_uri())+";\nimport fs from 'node:fs';\nconst m=JSON.parse(fs.readFileSync(process.argv[2]));const xs=JSON.parse(fs.readFileSync(process.argv[3]));const start=performance.now();const scores=xs.map(x=>infer(m,{version:m.version,vectors:{}},x));console.log(JSON.stringify({scores,milliseconds:performance.now()-start}));")
        fixture=Path(tmp)/'inputs.json';fixture.write_text(json.dumps(inputs.tolist()))
        res=json.loads(subprocess.check_output([a.node,str(script),str((a.run/'ranking.json').resolve()),str(fixture)]))
    with torch.no_grad(): scores=m(torch.from_numpy(inputs)).flatten().sigmoid().numpy()
    error=float(np.max(np.abs(scores-np.array(res['scores']))))
    report={'samples':1000,'max_absolute_error':error,'typescript_1000_inferences_ms':res['milliseconds'],'hardware':'development Mac / Node, not mobile hardware','ranking_json_bytes':(a.run/'ranking.json').stat().st_size,'checkpoint_bytes':(a.run/'ranking.pt').stat().st_size}
    (a.run/'parity-report.json').write_text(json.dumps(report,indent=2));print(report)
    if error>1e-6: raise ValueError('Inference parity failed')

if __name__=='__main__': main()
