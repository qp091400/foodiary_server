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
- ZIP과 DB는 Git·압축본(`package_release.py`)에 들어가지 않는다. 소스 업로드(`.gcloudignore`)에도 들어가지 않는다.
- **DB는 빌드마다 올리지 않는다.** `build`가 레시피 버전(`recipe-package.json`의 해시)마다 한 번만 빌드용 버킷 `gs://<프로젝트>-<서비스>-builds/recipe-seed/<해시>/`에 올리고, Cloud Build가 거기서 받아 이미지에 넣는다. 버킷에 이미 있으면 올리지 않고(그때는 Cloud Shell의 `recipe_data/`가 없어도 된다) 소스만 올라간다. 처음 한 번, 그리고 레시피 버전을 바꾼 뒤 첫 `build`에서만 약 830MB를 올린다. 받은 DB는 Docker 빌드가 SHA-256을 `recipe-package.json`과 다시 비교하므로 틀린 파일이나 깨진 파일이면 빌드가 실패한다.
- 이미지가 800MB쯤 커져서 첫 시작이 느려질 수 있다. Cloud Run에서의 시간은 아직 측정하지 않았다.

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
- `build`: 전용 계정으로 Cloud Build에서 `cloudrun` 타깃을 빌드한다. 모델 SHA256이 기존 검증값과 다르면 실패한다. 소스에는 `.gcloudignore`도 적용한다(레시피 DB는 소스에 넣지 않고 빌드용 버킷에서 받는다). 완료한 이미지는 digest로 고정한다.
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

### 서버가 자주 꺼지는 것처럼 느껴질 때 (콜드 스타트)

최소 인스턴스를 두지 않으므로, 한동안 요청이 없으면 Cloud Run이 인스턴스를 내리고(보통 15분 안팎이지만 보장된 시간은 아니다) 그 뒤 첫 요청은 OCR 모델과 레시피 DB를 다시 올리느라 오래 걸린다. 서버가 죽는 것이 아니라 잠드는 것이다. 요청이 오면 다시 켜진다.

- **앱이 켜져 있는 동안**: 앱이 켜질 때, 앞으로 돌아올 때, 그리고 5분마다 `GET /health`를 보낸다(`foodiary_app`의 `src/lib/api/keep-alive.ts`). 응답은 쓰지 않고 실패해도 무시하며, 뒤로 가 있는 동안에는 보내지 않는다. 로그인 화면을 보는 동안 서버가 먼저 깨어나므로 로그인이 덜 느려진다.
- **앱을 아무도 안 쓰는 시간에도 깨워 두려면**: Cloud Scheduler가 5분마다 `/health`를 부르게 한다. Cloud Shell에서 한 번만 만든다(이 저장소의 `cloudrun.py`가 만들어 주지는 않는다).

```sh
gcloud services enable cloudscheduler.googleapis.com
gcloud scheduler jobs create http foodiary-keepalive \
  --location asia-northeast1 \
  --schedule "*/5 * * * *" \
  --http-method GET \
  --uri "https://YOUR-SERVICE-URL/health"
# 잠시 멈춤 / 다시 시작 / 없애기
gcloud scheduler jobs pause foodiary-keepalive --location asia-northeast1
gcloud scheduler jobs resume foodiary-keepalive --location asia-northeast1
gcloud scheduler jobs delete foodiary-keepalive --location asia-northeast1
```

  `YOUR-SERVICE-URL`은 `publish`가 알려 주는 공개 주소다. 서비스를 `deploy`한 직후 `publish` 전까지는 IAM 비공개라 이 요청이 403을 받을 뿐 해롭지 않다. 리전은 서비스와 같게 둔다(서울이면 `asia-northeast3`).
- **`/health`는 DB를 건드리지 않는다.** 그래서 Neon의 절전을 방해하지 않는다. 서버가 깨어 있어도 Neon이 5분쯤 쉬었다면 그 뒤 첫 DB 요청은 조금 느릴 수 있다. 위의 `/ready` 감시 금지는 그대로다(`/ready`는 DB를 건드린다). `HEAD /health`도 받으므로 외부 감시 서비스를 써도 된다.
- 요청 기반 과금에서는 요청을 처리하지 않는 인스턴스의 CPU·RAM은 과금되지 않는다([요금](https://cloud.google.com/run/pricing)). 5분마다 가벼운 요청 하나는 요청 수 무료 한도에 비해 아주 작지만 이 구성에서 실제로 측정하지는 않았으니 청구 콘솔에서 확인한다. Cloud Scheduler는 결제 계정마다 작업 3개까지 무료다([요금](https://cloud.google.com/scheduler/pricing)).
- 최소 인스턴스를 1로 두는 방법(`--min-instances 1`)도 있지만 CPU 8개·RAM 8GiB 인스턴스가 항상 떠 있게 되어 유휴 비용이 크다. 이 구성에서는 쓰지 않는다.

### 웹 앱에 도메인 연결 (foodiary.kr)

도메인은 **웹 앱**(Firebase Hosting `foodiary-app-ef2b5`)에 붙인다. API 서버는 `run.app` 주소를 그대로 쓴다. 사용자에게 API 주소는 보이지 않고, 그래야 앱 설정(`EXPO_PUBLIC_API_URL`)도 바꾸지 않는다. 서버 응답에는 `X-Robots-Tag: noindex`가 붙어 검색에 나오지 않는다.

1. **Firebase에 도메인 추가**: Firebase 콘솔 → Hosting → 사이트 `foodiary-app-ef2b5` → 맞춤 도메인 추가 → `foodiary.kr`. 콘솔이 보여 주는 레코드(소유 확인용 TXT, 연결용 A)를 등록업체(가비아)의 DNS 설정에 **콘솔에 나온 값 그대로** 입력한다. `www.foodiary.kr`도 쓰려면 같은 방식으로 추가하고 `foodiary.kr`로 리디렉션하게 한다. 인증서는 자동으로 발급되며 몇 분에서 몇 시간 걸릴 수 있다.
2. **서버가 새 주소를 허용하게 하기**(브라우저 요청 허용, CORS). Cloud Shell에서 현재 허용 목록을 보고(비밀값은 출력하지 않는다) 추가한 뒤 배포한다.

   ```sh
   cd ~/foodiary-cloudrun
   python3 -c "import json; print(json.load(open('.cloudrun/config.json'))['origins'])"
   python3 scripts/cloudrun.py add-origin https://foodiary-app-ef2b5.web.app https://foodiary.kr https://www.foodiary.kr
   python3 scripts/cloudrun.py release     # 새 소스와 함께 올릴 때. 설정만 바꿨다면 deploy 다음 publish
   ```

   `add-origin`은 있던 주소를 지우지 않고(중복과 끝의 `/`는 정리) 클라우드에는 아무것도 하지 않는다. 설정은 다음 `deploy`부터 적용된다.
3. **웹 다시 빌드·배포**: 웹 앱을 평소 하던 방식으로 다시 내보내 Firebase Hosting에 올린다. 검색용 제목·설명, `robots.txt`, `sitemap.xml`이 이때 함께 올라간다.
4. **검색 등록**(본인 계정으로 직접): Google Search Console과 네이버 서치어드바이저에 `https://foodiary.kr`을 등록해 소유를 확인하고 `https://foodiary.kr/sitemap.xml`을 제출한다. 소유 확인을 HTML 태그로 하면 받은 값을 `foodiary_app`의 `src/lib/seo.ts` 처음 화면 메타데이터에 `verification`(`google`, `other: { "naver-site-verification": "..." }`)으로 넣고 다시 배포한다. 파일로 하면 받은 파일을 `public/` 폴더에 넣는다.
5. **(선택) 옛 주소 정리**: `foodiary.kr`이 정상으로 열린 뒤에 `firebase.json`의 `redirects`로 `foodiary-app-ef2b5.web.app`을 새 주소로 보낼 수 있다. 기존 공유 링크가 옛 주소로 만들어져 있으면 그대로 열리게 두거나 리디렉션이 경로(`/share/...`)를 유지하는지 확인한다.

색인까지는 며칠에서 몇 주 걸리고 검색 순위는 보장되지 않는다.

## 5. 업데이트·복구

소스 변경 후 `build → migrate → deploy → verify → publish`를 실행한다. `deploy`는 다시 IAM 비공개로 바꾸므로
현재 공개 서비스는 점검 시간에 업데이트한다.

압축본을 만들어 올리는 대신 **GitHub에 푸시하면 이미지가 자동으로 만들어지게** 할 수도 있다. 설정과 사용법은 [AUTO_BUILD.md](AUTO_BUILD.md)다(그 방식에서는 배포를 `release --image 커밋번호`로 한다).

이 과정은 한 명령으로 실행할 수 있다. 새 압축본(`package_release.py`가 만든 것)을 Cloud Shell에 올린 뒤:

```sh
# 옛 소스를 지우되 설정·비밀(.cloudrun)과 레시피 DB(recipe_data)는 남긴다
find ~/foodiary-cloudrun -mindepth 1 -maxdepth 1 ! -name .cloudrun ! -name recipe_data -exec rm -rf {} +
tar -xzf ~/새-압축본.tar.gz -C ~/foodiary-cloudrun
cd ~/foodiary-cloudrun && python3 scripts/cloudrun.py release
```

- `release`는 `build → migrate → deploy → publish`를 차례로 실행하고 **처음 실패한 단계에서 멈춘다**(`publish`가 공개 전에 `verify`와 같은 비공개 검사를 먼저 하므로 `verify`를 따로 부르지 않는다). 시작할 때 지금 트래픽을 받는 리비전 이름을 알려 주고, `deploy` 이후에 실패하면 그 리비전으로 되돌리는 명령을 보여 준다.
- `build`와 `migrate`에서 실패하면 돌아가는 서비스는 바뀌지 않는다. `deploy`부터 `publish`가 끝날 때까지는 서비스가 비공개다.
- `deploy` 뒤에 실패했으면 `build`를 다시 하지 않도록 `deploy`(끝나지 않았을 때), `publish`를 하나씩 실행한다.
- 폴더를 통째로 지우지 않는다. `.cloudrun/`에는 설정과 비밀 파일이, `recipe_data/`에는 압축본에 들어 있지 않은 레시피 DB(약 830MB)가 있다. 통째로 지우면 `init`을 다시 해서 DB 주소와 SMTP 비밀번호를 입력해야 하고 JWT 키가 새로 만들어져 모든 사용자가 로그아웃되며, 레시피 DB도 다시 준비해야 한다.
- 무엇이 지워질지 먼저 보려면 위 `find` 명령의 `-exec rm -rf {} +`를 `-print`로 바꿔 실행한다. 이전 digest와 Secret Manager 버전을 기록해 둔다.
이미지 교체만으로 DB가 되돌아가지는 않는다. 스키마 변경과 이전 이미지의 호환성도 확인한다.

Neon 복원 기능과 별도로 DB 덤프와 사진을 외부에 보관하고 복원을 시험한다.
Oracle용 `scripts/backup.sh`는 Compose 전용이므로 Cloud Run 백업에 사용하지 않는다.
사진은 인증된 Cloud Shell에서 `gcloud storage cp --recursive gs://BUCKET_NAME ./private-receipt-backup`으로 복사할 수 있다.
덤프는 DB 버전과 호환되는 `pg_dump`를 사용하고 DB 암호를 명령 인자·터미널 로그에 남기지 않는다.
사진 버킷에는 보존 중인 영수증을 나이만으로 자동 삭제하는 규칙을 설정하지 않는다.
오래된 빌드 소스·이미지는 저장 요금이 누적되므로 사용/롤백 여부를 확인한 후 정리한다.
레시피 DB를 소스에 넣던 때의 빌드 소스는 한 번에 약 830MB라서 특히 쌓인다. 진행 중인 빌드가 없을 때 정리한다(`recipe-seed/`는 지우지 않는다):

```sh
gcloud storage ls -l 'gs://<프로젝트>-<서비스>-builds/source/'     # 먼저 무엇이 있는지 본다
gcloud storage rm 'gs://<프로젝트>-<서비스>-builds/source/**'       # 소스 보관본만 지운다
gcloud storage ls 'gs://<프로젝트>-<서비스>-builds/recipe-seed/*/'  # 레시피 버전별 DB. 쓰지 않는 옛 버전은 폴더째 지운다
```

`recipe-seed/`를 실수로 지워도 괜찮다. 다음 `build`가 Cloud Shell의 `recipe_data/`에서 다시 올린다(그 폴더도 없으면 위 "레시피 데이터 준비"부터 다시 한다).

참조: [Storage 볼륨](https://docs.cloud.google.com/run/docs/configuring/services/cloud-storage-volume-mounts),
[메모리 설정](https://docs.cloud.google.com/run/docs/configuring/services/memory-limits),
[개발자 크레딧 적용](https://developers.google.com/profile/help/benefits#google-cloud-credits).

## 도메인 하나에서 웹과 API 제공 (기존 API 서비스가 프록시)

프로젝트가 다른 Firebase Hosting도 기존 Cloud Run API 서비스에서 HTTP로 전달할 수 있다.
Firebase에 Cloud Run rewrite를 추가하는 방식은 사용하지 않는다.
배포 설정 JSON에 다음 두 필드를 추가한다(비밀값이 아님).

```json
{
  "web_upstream": "https://foodiary-app-ef2b5.web.app",
  "public_origin": "https://foodiary.kr"
}
```

기존 설정의 나머지 필드는 유지한다. `web_upstream`을 비우면 기존 API 전용 동작을 유지한다.
활성화하면 `/api/*`는 같은 서비스의 API로, 나머지 GET/HEAD는 웹 upstream으로 전달한다.
상태 검사 `/health`, `/ready`는 기존 Cloud Run 검사와 호환되도록 API에서 처리한다.
API 토큰과 Cookie는 정적 웹 upstream으로 보내지 않는다.

앱/웹은 `EXPO_PUBLIC_API_URL=https://foodiary.kr/api`로 새로 빌드한다.
게이트웨이 모드에서는 이전 `/auth/login` 같은 비접두사 API 주소가 동작하지 않으므로
기존 모바일 클라이언트도 `/api` 주소로 갱신해야 한다.
운영 API 코드 재배포 후 `foodiary.kr`을 이 Cloud Run 서비스의 사용자 도메인으로 연결하고,
웹도 새 API 주소로 빌드하여 기존 Firebase Hosting에 배포한다.
DNS를 Cloud Run의 조회 시점 IP에 직접 고정하지 말고 사용자 도메인 연결에서 안내한 레코드를 사용한다.
기존에 도메인이 Firebase에 연결돼 있으면 기존 연결을 확인하고 Cloud Run 쪽으로 이전해야 한다.
`web_upstream`은 루프 방지를 위해 사용자 도메인 대신 Firebase의 원래 `.web.app` 주소를 사용한다.

이 저장소의 코드 수정만으로 Cloud Run 재배포, 사용자 도메인 연결, DNS 변경이 수행되지는 않는다.
