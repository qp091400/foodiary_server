import hashlib
import json
import sqlite3
import zipfile
import pytest
from scripts.prepare_recommendation_model import prepare,verify

@pytest.fixture
def package(tmp_path):
    weights=b'{"version":"test","dimension":32}'
    (tmp_path/'recommendation-model.json').write_bytes(weights)
    (tmp_path/'recipe-package.json').write_text(json.dumps({'sha256':'a'*64}))
    dbfile=tmp_path/'vectors.sqlite3'
    with sqlite3.connect(dbfile) as db:
        db.execute('CREATE TABLE metadata(version TEXT)');db.execute("INSERT INTO metadata VALUES ('test')")
        db.execute('CREATE TABLE embeddings(id TEXT,vector TEXT)');db.execute('INSERT INTO embeddings VALUES (?,?)',('a',json.dumps([0]*32)))
    checksum=lambda data:hashlib.sha256(data).hexdigest()
    manifest={'version':'test','ranking_sha256':checksum(weights),'embeddings_sha256':checksum(dbfile.read_bytes()),'recipe_count':1}
    archive=tmp_path/'test.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('ranking.json',weights);z.write(dbfile,'embeddings.sqlite3');z.writestr('manifest.json',json.dumps(manifest));z.writestr('training-report.json','{}')
        z.writestr('../outside.txt','must not extract arbitrary paths')
    config={'repository':'test/private','release':'v1','asset':'test.zip','sha256':checksum(archive.read_bytes()),'ranking_sha256':checksum(weights),'recipe_package_sha256':'a'*64,'model_version':'test'}
    (tmp_path/'recommendation-package.json').write_text(json.dumps(config))
    return tmp_path,archive

def test_release_install_is_verified_and_reused_without_network(package,monkeypatch):
    root,archive=package;target=prepare(root,archive)
    assert verify(target,root)==target
    assert not (root/'recipe_data/outside.txt').exists()
    monkeypatch.setattr('scripts.prepare_recommendation_model.download_release',lambda *args:pytest.fail('Matching cache should not download'))
    assert prepare(root)==target

def test_corrupt_archive_preserves_existing_seed(package):
    root,archive=package;target=prepare(root,archive)
    before=(target/'embeddings.sqlite3').read_bytes()
    archive.write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='ZIP checksum'):prepare(root,archive)
    assert (target/'embeddings.sqlite3').read_bytes()==before

def test_tracked_weights_and_recipe_pin_cannot_silently_drift(package):
    root,archive=package
    (root/'recommendation-model.json').write_text('changed')
    with pytest.raises(ValueError,match='Tracked model'):prepare(root,archive)
