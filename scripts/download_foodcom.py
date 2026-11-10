"""Download the original author's public Kaggle archive, retaining provenance."""
import argparse
import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

REF='shuyangli94/food-com-recipes-and-user-interactions'
BASE='https://www.kaggle.com/api/v1/datasets/'


def main():
    p=argparse.ArgumentParser()
    p.add_argument('output',type=Path)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    metadata=json.load(urllib.request.urlopen(BASE+'view/'+REF,timeout=60))
    (a.output/'source-metadata.json').write_text(json.dumps(metadata,indent=2))
    print('Source license:',metadata['licenseName'])
    archive=a.output/'foodcom.zip'
    urllib.request.urlretrieve(BASE+'download/'+REF,archive)
    hashes={}
    with zipfile.ZipFile(archive) as z:
        for name in ['RAW_recipes.csv','RAW_interactions.csv']:
            data=z.read(name);(a.output/name).write_bytes(data)
            hashes[name]=hashlib.sha256(data).hexdigest()
    (a.output/'provenance.json').write_text(json.dumps({'ref':REF,'download_url':BASE+'download/'+REF,'license':metadata['licenseName'],'sha256':hashes},indent=2))
    print(hashes)

if __name__=='__main__': main()
