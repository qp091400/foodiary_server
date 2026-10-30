# Foodiary: Google Cloud Run 배포

2026-10-21에 준비한 구성이고, 2026-10-23에 첫 비공개 배포를, 2026-10-24에 공개(`publish`)를 마쳤다.
로컬 검증과 실제 클라우드 검증은 `CLOUD_RUN_VALIDATION.md`에서 구분한다.

## 구성과 비용

- Cloud Run: CPU 8개와 RAM 8GiB에 OCR 엔진 2개(엔진마다 스레드 4개, 모델 하나, 메모리 1~2GB)로 사진 2장을 동시에 읽는다. 엔진 4개(스레드 2개씩)를 먼저 올려 봤더니 5장은 31초 → 20.8초로 줄었지만 사진 한 장을 읽는 시간이 6초 → 11초로 늘었다(같은 조건 로컬 측정: 한 장 3.2초 → 5.0초, 5장은 거의 같음). 그래서 2 × 4로 바꿨다. 엔진 하나가 CPU를 다 쓰지 못해서(CPU 2개 장당 2.3초 → 4개 1.5초 → 8개 1.2초, 내 PC) 엔진을 여러 개 두는 쪽이 8개를 제대로 쓴다. Cloud Run 실측(CPU 4개, 엔진 1개): 5장 31~33초. 새 설정의 시간은 배포 후 측정. 워커 1개, 최소 인스턴스 0, 서비스/리비전 최대 1, 동시 HTTP 요청 20(OCR은 한 번에 한 장이고 나머지는 줄을 서서 기다리므로, 기다리는 요청이 로그인·목록 같은 가벼운 요청을 막지 않게 넉넉히 둔다).
- 과금: 요청 기반. 유휴 상태를 위해 최소 인스턴스를 유지하지 않는다. 첫 요청에는 모델 초기화 지연이 있다.
- 기본 리전: 도쿄 `asia-northeast1`. Tier 1 요금과 한국에서의 거리를 고려한 초기값이다. 서울을 원하면 `init --region asia-northeast3`로 지정하고 해당 리전 요금을 다시 계산한다.
- OCR: 기존 PaddleOCR CPU 모델. 모델 파일 9개를 이미지 빌드 때 검증·포함하고 런타임에는 로컬 경로로 읽는다. 영수증을 Gemini API에 보내지 않는다.
- DB: Neon Free PostgreSQL. Cloud SQL, 로드밸런서, VPC 커넥터는 만들지 않는다.
- 사진: Cloud Run의 임시 디스크 대신 비공개 Cloud Storage 버킷을 `/app/uploads`에 마운트한다. 재배포 후에도 상대 경로를 그대로 사용한다.
- 비밀값: DB URL·JWT 키·SMTP 앱 비밀번호를 Secret Manager에 별도 저장하고 버전 번호를 고정한다. 서비스 계정 비밀키 파일을 발급하지 않는다.

[Pro 월 $10 크레딧](https://developers.google.com/program/plans-and-pricing)은 본인 구독 계정의
[개발자 혜택](https://developers.google.com/program/my-benefits)에서 Google Cloud 청구 계정에 적용해야 한다.
가족 공유만 받는 계정에는 개발자 혜택이 공유되지 않는다.

크레딧과 무료 한도는 결제 차단 장치가 아니다. 최대 인스턴스 제한도 월 지출 한도가 아니며 배포 전환 중에는 일시적으로 초과할 수 있다.
청구 콘솔에서 이 프로젝트를 대상으로 월 $5/$8/$10 예산 알림을 설정한다. 알림만으로 서비스나 결제가 자동 중단되지는 않는다.
Cloud Build, Artifact Registry 이미지 보관, Storage, Secret Manager, 로그, 데이터 전송도 합계에 포함한다.
같은 청구 계정의 다른 프로젝트가 무료 한도와 크레딧을 소비할 수 있다.

설명용 계산: 도쿄 요청 기반 단가, 다른 무료 한도 사용 없음, **영수증 1장당 시작 시간을 포함한 유료 실행 30초라고 가정**한 Cloud Run CPU·RAM 비용이다.
30초는 실제 Cloud Run 측정값이 아니다. DB·사진·빌드·이미지·로그·네트워크·세금 비용은 아래 표에 포함하지 않았다.

| 월 영수증 | 가정 실행 시간 | 무료 한도 적용 후 Cloud Run 계산 | Pro $10 적용 후 계산 |
|---|---:|---:|---:|
| 100장 | 3,000초 | $0 | $0 |
| 1,000장 | 30,000초 | $0 | $0 |
| 3,000장 | 90,000초 | $0.90 | $0 |
| 10,000장 | 300,000초 | $15.18 | $5.18 |

```sh
python3 scripts/estimate_cloudrun.py --receipts 1000 --seconds-per-receipt 30
```

계산기 `--other-google-costs`로 다른 Google 비용을 추가한다. 무료 한도가 이미 소진됐으면 `--no-free-tier`를 쓴다.
출처: [Cloud Run 과금·리전별 요금](https://cloud.google.com/run/pricing), [이미지 보관 요금](https://cloud.google.com/artifact-registry/pricing), [Neon 요금제](https://neon.com/pricing).

## 1. 계정에서 준비할 것

1. Pro를 결제하는 개인 계정으로 개발자 혜택을 활성화하고 월 $10 크레딧을 적용한다.
2. Google Cloud 프로젝트를 만들고 청구 계정을 연결한다. 프로젝트 **ID**와 숫자로 된 **번호**를 확인한다. 카드·비밀번호는 채팅에 보내지 않는다.
3. Neon Free 프로젝트를 만든다. Neon에는 도쿄·서울 리전이 없으므로 도쿄 Cloud Run과 가장 가까운 AWS 싱가포르(`aws-ap-southeast-1`)를 고른다. 리전은 만든 뒤 바꿀 수 없다. 무료 한도는 콘솔에서 확인하고 유료 플랜으로 전환하지 않는다.
4. 전용 DB 역할의 암호를 **24자 이상 무작위 값**으로 설정한다. Neon이 만드는 기본 암호는 16자라 반드시 변경해야 한다. 콘솔의 비밀번호 재설정은 짧은 값을 주므로 SQL Editor에서 `ALTER ROLE ... WITH PASSWORD '...'`로 정한다(암호를 채팅·화면 공유에 노출하지 않는다). 마이그레이션이 있으므로 풀링(`-pooler`)을 끈 직접 연결 URL을 쓴다. DB 이름과 연결 URL을 준비한다.
5. SMTP 발송 계정과 앱 비밀번호를 준비한다. Gmail이면 본인이 계정 화면에서 2단계 인증과 앱 비밀번호를 설정한다. Pro 구독은 SMTP 발송 권한을 대신하지 않는다.
6. 브라우저 프런트엔드가 있으면 실제 HTTPS 출처를 준비한다. 없으면 API 자신의 출처로 시작하고 프런트 배포 후 `origins`를 수정한다.

Neon 플러그인을 연결해 DB를 준비할 수도 있다. 연결하지 않았다면 콘솔에서 Free DB만 생성하면 된다.
기존 로컬 DB 데이터는 자동으로 이전하지 않는다. 이전이 필요하면 원본 백업과 별도 DB에 복원 시험 후 진행한다.

## 2. 소스 업로드와 비밀값 입력

PC에서 공개 가능한 소스만 묶는다:

```powershell
python -X utf8 scripts/package_release.py --output artifacts/foodiary-cloudrun.tar.gz
```

Google Cloud 콘솔의 **Cloud Shell**을 연다. 파일 업로드 메뉴로 `foodiary-cloudrun.tar.gz`를 올린다.
Cloud Shell은 이미 로그인한 사용자의 권한으로 실행된다. 이후 명령은 새 작업 폴더에서 실행한다:

```sh
mkdir -p ~/foodiary-cloudrun
tar -xzf ~/foodiary-cloudrun.tar.gz -C ~/foodiary-cloudrun
cd ~/foodiary-cloudrun
python3 scripts/cloudrun.py init --project YOUR_PROJECT_ID --project-number YOUR_PROJECT_NUMBER
python3 scripts/cloudrun.py plan
```

`init`은 SMTP 이메일, 발신 주소, DB URL, SMTP 앱 비밀번호를 묻는다. DB URL과 비밀번호는 화면에 표시하지 않는다.
`--origin https://your-frontend.example.com`을 추가하면 해당 프런트 출처로 시작한다.
DB URL은 psycopg 형식으로 정규화하고 TLS·접속 제한 시간을 설정한다.
SSL 옵션이 없으면 `sslmode=verify-full&sslrootcert=system`을 사용한다. `sslmode=disable`은 거부한다.

`.cloudrun/`은 본인만 접근하도록 만들며 Git·Docker·Cloud Build·배포 압축본에 포함하지 않는다.
이 폴더는 DB/SMTP 연결 정보와 JWT 키를 포함하므로 공유하지 않는다. `init`은 기존 파일을 덮어쓰지 않는다.
계정 정보 없이 오프라인 계획만 보려면:

```sh
python3 scripts/cloudrun.py --config deploy/cloudrun.config.example.json plan
```

### 레시피 데이터 준비 (빌드 전에 처음 한 번)

레시피 추천 DB(약 830MB)는 Git에 없고, `build`가 Cloud Run 이미지 안에 읽기 전용으로 넣는다(`RECIPE_DATA_DIR=/opt/recipe-data`). 준비돼 있지 않으면 `build`가 업로드 전에 멈춘다. Cloud Shell에서 둘 중 하나로 `~/foodiary-cloudrun/recipe_data/image-seed/`를 만든다:

```sh
# A. 공유받은 ZIP(약 160MB)을 Cloud Shell 업로드 메뉴로 올렸을 때
python3 -m scripts.prepare_recipes --package ~/Foodiary-recipes-20261029-v4.zip

# B. 비공개 저장소에서 직접 받을 때 (GitHub 계정에 접근 권한 필요)
gh auth login
python3 -m scripts.prepare_recipes
```

- `python3 -m scripts.start_with_recipes`는 `prepare_recipes`의 옛 이름이다.
- 준비된 폴더는 다음 소스 업로드(`tar -xzf`) 때 그대로 남는다. 레시피 버전을 바꿀 때만(`recipe-package.json`) 다시 준비한다.
- 이미지는 `recipe-package.json`에 적힌 해시와 맞는 DB가 아니면 빌드에서 실패한다.
- ZIP과 DB는 Git·압축본(`package_release.py`)에 들어가지 않는다. 업로드는 `.gcloudignore`가 준비된 DB와 manifest만 통과시킨다.
- 이미지가 800MB쯤 커져서 빌드 업로드와 첫 시작이 느려질 수 있다. Cloud Run에서의 시간은 아직 측정하지 않았다.

## 3. 리소스 생성과 비공개 배포

이 단계부터 실제 리소스와 비용이 발생할 수 있다. 크레딧 적용·예산 알림·`plan`의 프로젝트를 먼저 확인한다.
프로젝트 소유자 계정으로 실행하는 초기 소규모 배포를 대상으로 한다. 조직 정책이 공개 호출을 금지하면 관리자가 정책을 검토해야 한다.

```sh
python3 scripts/cloudrun.py setup
python3 scripts/cloudrun.py secrets
python3 scripts/cloudrun.py build
python3 scripts/cloudrun.py migrate
python3 scripts/cloudrun.py deploy
python3 scripts/cloudrun.py verify
```

- `setup`: 필요한 API, 전용 런타임/마이그레이션/빌드 계정, 이미지 저장소, 사진/빌드 소스 버킷, 비밀 저장소를 준비한다. 버킷에 공개 접근 차단과 균일한 버킷 권한을 적용한다.
- `secrets`: 로컬 비밀 파일을 Secret Manager로 올린다. 비밀값을 명령 인자나 로그에 출력하지 않는다. 다시 실행하면 새 버전을 만들고 다음 배포가 사용할 버전 번호를 기록한다.
- `build`: 전용 계정으로 Cloud Build에서 `cloudrun` 타깃을 빌드한다. 모델 SHA256이 기존 검증값과 다르면 실패한다. 소스에는 `.gcloudignore`도 적용한다. 완료한 이미지는 digest로 고정한다.
- `migrate`: DB 비밀값만 읽을 수 있는 별도 계정으로 작업 1개를 실행한다. OCR을 로드하지 않으며 재시도는 0이다. 성공한 이미지에만 배포를 허용한다.
- `deploy`: 초기 접근을 IAM 비공개로 둔다. 비root 사용자 10001로 사진 버킷에 접근한다. 사진 볼륨이 없거나 쓸 수 없으면 시작하지 않는다. 실제 OCR 시작 추론이 통과해야 포트가 열린다.
- `verify`: 임시 Google ID 토큰을 별도 헤더에 넣고 HTTPS로 OCR/DB 준비, 앱 인증, 문서·개발 페이지 차단을 읽기 전용으로 검사한다. 토큰을 출력하지 않는다.
  테스트 비밀번호를 설정했으면 `/receipt-test` 페이지가 열려 있고 비밀번호 없는 인식 요청을 401로 거절하는지도 본다. 설정하지 않았으면 그 주소도 404여야 한다.

전용 계정 권한은 런타임=자신의 사진 버킷 객체·필요한 3개 비밀, 마이그레이션=DB 비밀 1개,
빌드=자신의 이미지 저장소 쓰기·빌드 소스 읽기·로그 쓰기로 나눈다.
IAM 전파가 늦거나 명령이 실패하면 오류를 확인하고 해당 단계부터 다시 실행한다.
FUSE 동작·IAM·Neon TLS는 실제 계정에서 첫 배포 때 검증한다.

## 4. 공개와 실제 영수증 검사

```sh
python3 scripts/cloudrun.py publish
```

`publish`는 비공개 검사를 다시 통과한 후 **해당 API의 공개 호출 권한**을 열고 공개 HTTPS 검사를 실행한다.
사진 버킷은 공개하지 않으며 API의 JWT 인증은 그대로 유지한다. 기본 `run.app` HTTPS 주소를 사용하므로 도메인을 살 필요가 없다.
휴대폰에서 실제 메일 수신 → 회원가입/로그인 → 사진 업로드 → 초안 수정 → 확정 → 통계까지 확인한다.
같은 사진의 품목·금액을 로컬 결과와 비교하고 Cloud Run의 응답 시간과 메모리를 기록한다.

### 영수증 인식 테스트 페이지 (선택)

공개 주소의 `/receipt-test`에서 휴대폰으로 영수증 인식을 시험할 수 있다. 테스트 비밀번호를 정했을 때만 열린다.

```sh
python3 scripts/cloudrun.py test-password
python3 scripts/cloudrun.py deploy
python3 scripts/cloudrun.py verify
python3 scripts/cloudrun.py publish
```

`test-password`는 비밀번호를 두 번 묻는다(화면에 보이지 않는다). 영문·숫자·기호 12자 이상이고 공백은 쓸 수 없다.
비밀 저장소 `<서비스>-receipt-test-password`에 새 버전으로 올리고, 런타임 계정에 그 비밀만 읽을 권한을 준다.
비밀번호를 바꿀 때도 같은 순서로 실행한다. 끄려면 `test-password --disable` 뒤에 `deploy → verify → publish`를 실행한다.
새 코드를 배포할 때와 함께 하려면 `build → migrate`를 먼저 실행한다. `secrets`를 다시 실행해도 테스트 비밀번호는 유지된다.
이 페이지는 아무것도 저장하지 않고, 틀린 비밀번호를 포함한 모든 요청이 영수증 업로드와 같은 분당 한도를 함께 쓴다.

Cloud Run에서는 전달 IP 헤더를 신뢰하지 않으며 초기 요청 제한은 **인스턴스 전체** 인증 30회/분·영수증 업로드 30회/분(사진 한 장이 한 번)이다.
여러 사용자가 한도를 공유하고 재시작하면 카운터가 초기화된다. OCR 엔진은 2개이고 엔진마다 전용 스레드가 하나다. 동시에 온 사진은 요청(사람) 번호 순서로 엔진에 배정한다(한 사람의 사진을 먼저 읽고, 엔진이 남으면 다음 사람 것도 읽는다). 줄에 선 사진이 `OCR_QUEUE_MAX`를 넘거나 기다리다 `OCR_QUEUE_WAIT_SECONDS`가 지나면 503/Retry-After를 반환한다. 엔진의 모델은 서버가 켜질 때 하나씩 올라오므로(로컬 측정: 1개 4.3초, 2개 약 6초, 4개 8.2초) 첫 시작이 그만큼 늦어진다.
초기 소규모 시험용 보호다. 다중 인스턴스나 실제 사용자를 늘리기 전에는 공유 요청 제한과 트래픽 보호를 설계한다.
주기적인 `/ready` 외부 감시는 Neon의 절전을 방해할 수 있으므로 무료 한도 시험 중에는 상시 폴링하지 않는다.

## 5. 업데이트·복구

소스 변경 후 `build → migrate → deploy → verify → publish`를 실행한다. `deploy`는 다시 IAM 비공개로 바꾸므로
현재 공개 서비스는 점검 시간에 업데이트한다. 이전 digest와 Secret Manager 버전을 기록해 둔다.
이미지 교체만으로 DB가 되돌아가지는 않는다. 스키마 변경과 이전 이미지의 호환성도 확인한다.

Neon 복원 기능과 별도로 DB 덤프와 사진을 외부에 보관하고 복원을 시험한다.
Oracle용 `scripts/backup.sh`는 Compose 전용이므로 Cloud Run 백업에 사용하지 않는다.
사진은 인증된 Cloud Shell에서 `gcloud storage cp --recursive gs://BUCKET_NAME ./private-receipt-backup`으로 복사할 수 있다.
덤프는 DB 버전과 호환되는 `pg_dump`를 사용하고 DB 암호를 명령 인자·터미널 로그에 남기지 않는다.
사진 버킷에는 보존 중인 영수증을 나이만으로 자동 삭제하는 규칙을 설정하지 않는다.
오래된 빌드 소스·이미지는 저장 요금이 누적되므로 사용/롤백 여부를 확인한 후 정리한다.

참조: [Storage 볼륨](https://docs.cloud.google.com/run/docs/configuring/services/cloud-storage-volume-mounts),
[메모리 설정](https://docs.cloud.google.com/run/docs/configuring/services/memory-limits),
[개발자 크레딧 적용](https://developers.google.com/profile/help/benefits#google-cloud-credits).
