"""deploy/auto_deploy.sh with fake gcloud and curl: what runs, in what order, and the rollback."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "auto_deploy.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

FAKE_GCLOUD = """#!/bin/bash
echo "gcloud $*" >> "$LOG"
case "$*" in
  *"services describe foodiary-ocr --format=value(metadata.name)"*) [ "$OCR_EXISTS" = yes ] || exit 1; echo foodiary-ocr ;;
  *"services describe foodiary-ocr --format=value(status.latestReadyRevisionName)"*) echo foodiary-ocr-00003 ;;
  *"services describe foodiary --format=value(status.latestReadyRevisionName)"*) echo foodiary-00012 ;;
  *"services describe foodiary --format=value(status.url)"*) echo https://api.example ;;
  *"jobs describe foodiary-push"*) exit 1 ;;
  *"jobs execute"*) [ "$MIGRATION" = ok ] || exit 1 ;;
esac
"""
FAKE_CURL = """#!/bin/bash
echo "curl $*" >> "$LOG"
[ "$READY" = ok ]
"""
FAKE_SLEEP = "#!/bin/bash\n"


def deploy(tmp_path, *, ocr_exists="yes", migration="ok", ready="ok", auto="true"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("gcloud", FAKE_GCLOUD), ("curl", FAKE_CURL), ("sleep", FAKE_SLEEP)):
        (bin_dir / name).write_text(body, newline="\n")
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "log.txt"
    log.write_text("")
    env = {
        **os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "LOG": str(log),
        "IMAGE": "registry/foodiary:abc1234", "REGION": "asia-northeast1", "SERVICE": "foodiary", "AUTO_DEPLOY": auto, "TEST_ACCOUNTS": "true",
        "OCR_EXISTS": ocr_exists, "MIGRATION": migration, "READY": ready,
    }
    # bash needs a POSIX path to the script on Windows too
    script = SCRIPT.as_posix() if os.name != "nt" else "/" + SCRIPT.as_posix().replace(":", "", 1)
    done = subprocess.run([BASH, script], env=env, capture_output=True, text=True)
    lines = [line.split(" --region")[0] for line in log.read_text().splitlines()]
    return done.returncode, lines, done.stdout + done.stderr


def test_a_push_migrates_then_swaps_the_image_of_ocr_then_api(tmp_path):
    code, lines, _ = deploy(tmp_path)
    assert code == 0
    changes = [line for line in lines if " describe " not in line]
    assert changes == [
        "gcloud run jobs update foodiary-migrate --image=registry/foodiary:abc1234",
        "gcloud run jobs execute foodiary-migrate --wait",
        "gcloud run services update foodiary-ocr --image=registry/foodiary:abc1234",
        "gcloud run services update foodiary --image=registry/foodiary:abc1234 --update-env-vars=TEST_ACCOUNTS_ENABLED=true",
        "gcloud run services update-traffic foodiary-ocr --to-latest",
        "gcloud run services update-traffic foodiary --to-latest",
        "curl -fsS --max-time 180 https://api.example/ready",
    ]


def test_a_failed_ready_check_goes_back_to_the_previous_revisions(tmp_path):
    code, lines, output = deploy(tmp_path, ready="down")
    assert code == 1
    assert sum(line.startswith("curl") for line in lines) == 3
    assert lines[-2:] == [
        "gcloud run services update-traffic foodiary --to-revisions=foodiary-00012=100",
        "gcloud run services update-traffic foodiary-ocr --to-revisions=foodiary-ocr-00003=100",
    ]


def test_a_failed_migration_changes_no_service(tmp_path):
    code, lines, _ = deploy(tmp_path, migration="broken")
    assert code != 0
    assert not any("services update" in line for line in lines)


def test_before_the_ocr_service_exists_nothing_is_changed(tmp_path):
    code, lines, output = deploy(tmp_path, ocr_exists="no")
    assert code == 1 and "release --image" in output
    assert all(" describe " in line for line in lines)


def test_switching_auto_deploy_off_only_builds(tmp_path):
    code, lines, output = deploy(tmp_path, auto="false")
    assert code == 0 and lines == [] and "off" in output
