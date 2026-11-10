# GitHub 푸시로 자동 빌드·배포

`main`에 푸시하면 Cloud Build가 Cloud Run 이미지를 만들고 **바로 배포까지 한다** (2026-11-10~, `deploy/auto_deploy.sh`).

- 자동 배포는 **이미지만 바꾼다**: 마이그레이션 잡 → `foodiary-ocr` → `foodiary` 순서로 `gcloud run ... update --image`. 환경 변수·비밀값·사진 버킷·공개 여부는 그대로라서 **배포 중에도 API는 공개 상태 그대로**다(`cloudrun.py deploy`처럼 비공개가 되지 않는다).
- 끝나면 API의 `/ready`(DB + OCR 서비스)를 3번까지 확인하고, 실패하면 두 서비스를 **바로 전 리비전으로 되돌리고** 빌드를 실패로 표시한다. 마이그레이션은 되돌리지 않으니, 마이그레이션은 이전 코드에서도 돌아가게 만든다.
- **설정을 바꿀 때**(CPU·메모리·환경 변수·`add-origin` 등)는 예전처럼 Cloud Shell에서 `deploy` → `publish`. 자동 배포는 설정을 건드리지 않는다.
- **`main`에 누가 푸시하든 배포된다.** 다른 사람의 푸시도 마찬가지다. 잠시 끄려면 트리거의 대체 변수 `_AUTO_DEPLOY`를 `false`로 바꾼다(빌드만 한다).
- `foodiary-ocr` 서비스가 아직 없으면(서버 분리 전) 아무것도 바꾸지 않고 멈춘다. 처음 한 번은 Cloud Shell에서 `release --image 커밋번호`로 두 서비스를 만든다.

| | 지금(압축본) | 자동 빌드 |
|---|---|---|
| 코드 전달 | 압축본 만들기 → Cloud Shell 업로드 → 풀기 | GitHub에 푸시 |
| 이미지 빌드 | `release`가 Cloud Shell에서 빌드까지 | 푸시하면 Cloud Build가 자동으로 |
| 배포 | `release` | 자동 (설정 변경만 `deploy` → `publish`) |

이미지에는 푸시한 커밋의 앞 7글자(`$SHORT_SHA`)와 `latest` 이름표가 붙는다. 옛 압축본 방식(`release`만 실행)도 그대로 쓸 수 있다.

> 이 설정은 Google Cloud 쪽 동작을 로컬에서 시험할 수 없었다. 로컬에서는 빌드 설정 파일의 형식, 셸 단계의 동작(치환 규칙을 흉내 내서 실행), `cloudrun.py`의 이미지 선택을 시험했다. 처음 한 번은 아래 3번의 시험 빌드로 실제 동작을 확인한다.

## 한 번만 하는 설정

### 0. 이 변경을 GitHub에 올린다
트리거는 GitHub의 `deploy/cloudbuild.trigger.yaml`을 읽는다. 이 파일이 있는 커밋이 `main`에 올라가 있어야 한다.

### 1. Cloud Build에 GitHub 연결 (Cloud Shell)
GitHub 저장소의 **관리자 권한이 있는 계정**으로 승인해야 한다. 리전은 서비스와 같은 `asia-northeast1`이다.
```sh
gcloud builds connections create github foodiary-github --region=asia-northeast1
```
출력된 링크를 브라우저에서 열어 GitHub에 로그인하고 **Cloud Build GitHub App**을 승인·설치한다(저장소 `qp091400/foodiary_server` 선택). 설치가 끝났는지 확인한다.
```sh
gcloud builds connections describe foodiary-github --region=asia-northeast1
```
`installationState`가 `COMPLETE`면 끝이다. 아니면 출력에 남은 단계의 링크가 있다. 이어서 저장소를 연결한다.
```sh
gcloud builds repositories create foodiary-server --remote-uri=https://github.com/qp091400/foodiary_server.git --connection=foodiary-github --region=asia-northeast1
```
API를 켜라는 안내가 나오면 켠다. 콘솔로 하려면 Cloud Build → 저장소 → 2세대 → 호스트 연결 만들기이다.

### 2. 트리거 만들기
`main`에 푸시될 때마다 `deploy/cloudbuild.trigger.yaml`로 빌드한다. 서비스 계정은 `cloudrun.py build`와 같은 빌드 전용 계정이다.
```sh
gcloud builds triggers create github \
  --name=foodiary-main-build \
  --repository=projects/virtual-primer-512512-g0/locations/asia-northeast1/connections/foodiary-github/repositories/foodiary-server \
  --branch-pattern='^main$' \
  --build-config=deploy/cloudbuild.trigger.yaml \
  --region=asia-northeast1 \
  --service-account=projects/virtual-primer-512512-g0/serviceAccounts/foodiary-build@virtual-primer-512512-g0.iam.gserviceaccount.com \
  --description="main 푸시마다 Cloud Run 이미지를 만든다 (배포는 하지 않음)"
```
저장소 전체가 이미지에 들어가므로(`Dockerfile`의 `COPY . .`) 문서나 테스트만 바꿔도 빌드된다. 파일로 걸러 내지 않은 이유는, 걸러 내면 어떤 커밋에는 이미지가 없어서 아래 `release --image 커밋번호`가 실패하기 때문이다.

### 3. 시험 빌드
```sh
gcloud builds triggers run foodiary-main-build --branch=main --region=asia-northeast1
gcloud builds list --region=asia-northeast1 --limit=3
```
Cloud Build → 기록(리전 `asia-northeast1`)에서 진행을 본다. 빌드는 약 7분 걸린다(수동 빌드 실측). 성공하면 이름표를 확인한다.
```sh
gcloud artifacts docker images list asia-northeast1-docker.pkg.dev/virtual-primer-512512-g0/foodiary --include-tags --limit=5
```
방금 빌드한 커밋의 앞 7글자와 `latest`가 보여야 한다. 실패하면 로그의 마지막 줄들과 오류 문구를 확인한다. 가능성이 높은 곳은 서비스 계정 권한과 GitHub 연결 승인이다.

### 4. Cloud Shell 폴더를 git으로 바꾼다
`~/foodiary-cloudrun`의 `.cloudrun`(설정·비밀)과 `recipe_data`는 `.gitignore`에 있어서 그대로 남는다. 저장소는 공개라서 로그인이 필요 없다.
```sh
cd ~/foodiary-cloudrun
git init -q && git remote add origin https://github.com/qp091400/foodiary_server.git
git fetch -q origin main && git checkout -f -B main origin/main
```

### 5. 자동 배포 권한 (한 번)
빌드 계정이 Cloud Run 서비스·잡의 이미지를 바꾸고 마이그레이션 잡을 실행할 수 있게 한다. 실행 계정(`-run`, `-migrate`)으로 돌게 하려면 그 계정을 "대신 쓸" 권한도 필요하다.
```sh
PROJECT=$(gcloud config get-value project)
BUILD=foodiary-build@$PROJECT.iam.gserviceaccount.com
gcloud projects add-iam-policy-binding $PROJECT --member=serviceAccount:$BUILD --role=roles/run.developer --condition=None
for ACCOUNT in foodiary-run foodiary-migrate; do
  gcloud iam service-accounts add-iam-policy-binding $ACCOUNT@$PROJECT.iam.gserviceaccount.com     --member=serviceAccount:$BUILD --role=roles/iam.serviceAccountUser
done
```
이미 만든 트리거는 그대로 둔다(`deploy/cloudbuild.trigger.yaml`을 다시 읽는다). 설명만 바꾸려면 콘솔에서 고친다.

### 6. (권장) 오래된 이미지 자동 정리
푸시마다 이미지가 하나씩 쌓이고, 이미지는 모델과 레시피 DB 때문에 크다. 최근 5개는 남기고 14일이 지난 것은 지운다. `policy.json`:
```json
[
  {"name": "keep-five-most-recent", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 5}},
  {"name": "delete-older-than-14-days", "action": {"type": "Delete"}, "condition": {"tagState": "any", "olderThan": "14d"}}
]
```
먼저 시험(`--dry-run`)으로 확인하고 적용한다.
```sh
gcloud artifacts repositories set-cleanup-policies foodiary --location=asia-northeast1 --policy=policy.json --dry-run
gcloud artifacts repositories set-cleanup-policies foodiary --location=asia-northeast1 --policy=policy.json --no-dry-run
```
Keep 정책이 Delete 정책보다 우선한다. 정책은 하루 안팎에 적용된다. **지워진 이미지를 쓰는 옛 Cloud Run 리비전은 새로 시작할 수 없다.** 되돌리기는 최근 리비전까지만 믿고, 오래된 리비전으로 되돌릴 일을 위해 필요한 이미지는 남겨 둔다.

## 평소 쓰는 법
1. GitHub Desktop에서 **Push**하면 빌드와 배포가 자동으로 시작된다. Cloud Build 기록(리전 `asia-northeast1`)에서 `SUCCESS`가 되면 반영된 것이다(약 10분). `FAILURE`면 로그 끝부분을 본다: 빌드에서 멈췄으면 서비스는 그대로이고, `/ready`에서 멈췄으면 이전 리비전으로 돌아가 있다.
2. 자동 배포를 껐거나 손으로 다시 배포하고 싶을 때는 Cloud Shell에서 같은 커밋을 받아 그 이미지로 배포한다. 창을 닫아도 계속되게 `nohup`을 쓴다.
```sh
cd ~/foodiary-cloudrun && git pull -q origin main
nohup python3 scripts/cloudrun.py release --image $(git rev-parse HEAD | cut -c1-7) > ~/release.log 2>&1 &
tail -f ~/release.log
```
- `release --image`는 이미지를 빌드하지 않고 `use_image → migrate → deploy → publish` 순서로 실행한다. 빌드가 아직 안 끝났거나 실패했으면 `No image tagged ...`로 시작 전에 멈추고 서비스는 바뀌지 않는다.
- `git rev-parse HEAD | cut -c1-7`은 지금 받은 커밋의 이미지 이름표다. 코드와 배포되는 이미지가 같은 커밋임을 보장한다. 가장 최근에 끝난 빌드를 쓰려면 `--image latest`지만, 빌드가 순서대로 끝나지 않을 수 있어 커밋번호를 권한다.
- 배포 중 접속이 끊기는 구간, 실패했을 때의 되돌리기 명령은 `release`와 같다([CLOUD_RUN.md](CLOUD_RUN.md)).
- 서버 설정만 바꿀 때(`add-origin` 등)는 이전처럼 `deploy` 다음 `publish`를 실행한다.

## 되돌리기 / 끄기
- 트리거를 끄려면 `gcloud builds triggers delete foodiary-main-build --region=asia-northeast1`(또는 콘솔에서 비활성화). 압축본 방식은 언제든 다시 쓸 수 있다.
- Cloud Shell을 git에서 압축본 방식으로 되돌릴 필요는 없다. 압축본을 풀면 그 파일들이 덮어써질 뿐이다.

## 비용과 한계
- 푸시마다 Cloud Build 시간이 쓰인다. 이 구성의 월 요금은 계산하지 못했으니 빌드 횟수가 많아지면 청구 콘솔에서 확인한다.
- 푸시마다 쌓이는 이미지 저장 요금은 위 5번의 정리 정책으로 줄인다.
- 자동 배포에는 승인 단계가 없다. 푸시가 곧 배포다. 배포 시점을 고르고 싶어지면 `_AUTO_DEPLOY=false`로 두고 위 2번처럼 손으로 배포한다.
- 이 자동 배포는 Google Cloud에서 아직 실행해 보지 않았다. 로컬에서는 가짜 `gcloud`·`curl`로 순서·되돌리기·멈춤을 시험했다(`tests/test_auto_deploy.py`). 첫 푸시 때 Cloud Build 로그를 지켜본다.
