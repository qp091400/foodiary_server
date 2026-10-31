# 냉장고 영수증 백엔드

마트 영수증 사진 한 장으로 냉장고 속 식품, 소비기한, 지출과 낭비를 관리하는 앱의 백엔드입니다.
FastAPI + PostgreSQL + PaddleOCR(로컬 모델)로 만들었고, 유료 OCR이나 외부 AI API는 쓰지 않습니다.

Google AI Pro의 월 $10 크레딧을 활용하는 Cloud Run 배포는 [deploy/CLOUD_RUN.md](deploy/CLOUD_RUN.md)를 따릅니다.
CPU OCR 모델을 이미지에 포함하고, Neon PostgreSQL과 비공개 사진 버킷을 연결하는 배포 도구가 준비되어 있습니다.
비밀값·운영 이미지·마이그레이션을 분리하고, 비공개 HTTPS 검증 후 API를 공개합니다.

`docker-compose.yml`은 로컬 개발용입니다.

## 1. 기능

- **영수증 등록 (2단계)**: 사진을 올리면 품목(이름·수량·단가·금액)과 예상 소비기한이 채워진 **초안**을 돌려주고, 사용자가 확인·수정해서 **확정**하면 그때 냉장고에 들어갑니다. OCR은 틀릴 수 있으므로 확인 없이 바로 넣지 않습니다.
- **소비기한 자동 추정**: 상품명으로 카테고리(우유 10일, 두부 7일, 냉동식품 90일 …)를 맞히고, 영수증에 상품코드가 있으면 식품안전나라 C005로 제품별 소비기한을 조회합니다.
- **용량 정리**: 상품명에 적힌 용량·중량·개수(`1L`, `200ml×6`, `600g`, `100매`)를 뽑아 `size_text`, `size_amount`(ml·g·개수로 환산한 총량), `size_unit`으로 돌려줍니다. 초안, 식품, 구매 기록 응답에 모두 들어갑니다.
- **냉장고 관리**: 목록, 임박 목록(지난 것 포함), 수동 추가·수정, 먹음/버림 처리(반 개도 가능).
- **구매 기록·통계**: 무엇을 몇 번, 얼마치 샀는지와 월별 식품 지출, 먹은 금액, 버린 금액, 낭비율.
- **냉장고 공유**: 초대코드(8자, 7일 유효)로 가족·룸메이트와 한 냉장고를 같이 씁니다.
- **계정**: 아이디 + 이메일 가입, 로그인(JWT), 이메일 인증코드로 아이디 찾기·비밀번호 재설정.

영수증 하단 바코드는 거래번호만 담고 있어 품목을 알 수 없습니다. 그래서 **같은 영수증 중복 등록 방지**에만 씁니다.

## 2. 실행

### docker compose (권장)

최초 한 번 데이터를 준비한 뒤, Docker 빌드와 실행은 Compose로 수행합니다.

```bash
cp .env.example .env        # 없어도 기본값으로 뜹니다
gh auth login               # 다운로드할 GitHub 계정에 비공개 데이터 저장소 접근 권한 필요
python3 -m scripts.prepare_recipes
docker compose up --build -d
```

- 데이터 준비에는 Python 3.11+와 GitHub CLI가 필요합니다. 이미 ZIP이 있다면 `python3 -m scripts.prepare_recipes --package /absolute/path/to/package.zip`으로 준비할 수 있습니다(gh 불필요).
- 준비 스크립트는 다운로드와 검증만 수행합니다. 검증된 DB는 Git에서 제외된 `recipe_data/image-seed/`에 보관하고, 다운로드 ZIP과 임시 파일은 정리합니다. 같은 데이터가 준비돼 있으면 다시 다운로드하지 않습니다.
- 평소 코드 변경은 **`docker compose up --build -d`**로 반영합니다. 이때 데이터 다운로드나 gh 인증은 수행하지 않습니다. Docker Compose 2.17+가 필요합니다. 재시작만 할 때는 `docker compose up -d`, 로그 확인은 `docker compose logs -f api`를 사용합니다.
- 데이터 버전은 저장소 루트의 [recipe-package.json](recipe-package.json) 한 파일에서 관리합니다. 갱신 시 이 파일을 변경하고 `python3 -m scripts.prepare_recipes` → `docker compose up --build -d`를 실행합니다. 빌드는 준비된 DB가 이 설정과 맞는지 오프라인으로 검사합니다.
- 서버 시작 시 레시피가 같으면 유지하고, 다르면 기존 레시피 DB만 백업 후 교체합니다. 이전 이미지로 돌아가면 레시피도 그 이미지 버전으로 돌아갑니다.
- API: <http://localhost:8000>, Swagger: <http://localhost:8000/docs>
- 첫 빌드는 PaddleOCR 설치 때문에 5~10분 걸립니다.
- **첫 영수증 업로드는 모델을 내려받느라 느립니다** (인터넷 필요, Hugging Face에서 받습니다). 모델은 `paddle_models` 볼륨에 남아 재시작해도 다시 받지 않습니다.
- 볼륨: `pgdata`(사용자 DB), `uploads`(영수증 이미지), `paddle_models`(OCR 모델 캐시), `recipe_data`(레시피 DB)
- **PaddleOCR 모델 캐시 경로**: 컨테이너(root)의 `/root/.paddlex/official_models/` (= `~/.paddlex`). 실제 실행 로그로 확인했고, 받는 모델은 `PP-OCRv5_server_det`, `korean_PP-OCRv5_mobile_rec`, `PP-LCNet_x1_0_textline_ori` 세 개입니다.
- 확인한 버전: Python 3.11, `paddlepaddle==3.2.2`, `paddleocr==3.7.0`. paddlepaddle 3.3.1은 CPU 추론에서 오류가 나서 3.2.2로 고정했습니다 ([requirements-ocr.txt](requirements-ocr.txt)).

### 로컬 (venv)

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-ocr.txt  # 영수증 인식까지 쓸 때만. 없으면 OCR_ENGINE=none

cp .env.example .env                 # DATABASE_URL의 호스트(db)를 localhost로 바꾸기
docker run -d --name fridge-db -p 5432:5432 \
  -e POSTGRES_USER=fridge -e POSTGRES_PASSWORD=fridge -e POSTGRES_DB=fridge postgres:16
uvicorn app.main:app --reload
```

### 테스트

```bash
pytest
```

SQLite 인메모리 DB, console 메일, `OCR_ENGINE=none`으로 돌기 때문에 PostgreSQL이나 PaddleOCR이 없어도 됩니다.

### 영수증 인식 테스트 페이지 (개발용)

로그인 없이 사진만 올려서 인식 결과를 보는 페이지입니다. `.env`에 `DEV_TOOLS=true`를 넣고 서버를 다시 띄운 뒤
<http://localhost:8000/dev/receipt-test> 를 엽니다.

- 실제 업로드와 같은 코드(OCR → 파서 → 카테고리·소비기한 추정)를 돌리지만 **영수증도 이미지도 저장하지 않습니다.**
- 매장·구매일·총액, 읽어낸 품목, OCR 원문 줄, 걸린 시간을 보여 줍니다. 품목이 이상하면 원문 줄을 보고 OCR이 잘못 읽은 건지 파서가 잘못 해석한 건지 가릴 수 있습니다.
- 같은 Wi-Fi의 휴대폰에서 `http://PC의IP:8000/dev/receipt-test`로 열면 **사진 찍기** 버튼으로 바로 찍어서 올릴 수 있습니다. 큰 사진은 올리기 전에 브라우저에서 긴 변 2000px로 줄여 보냅니다.
- **로그인 없이 OCR을 돌리는 경로라서 운영에서는 반드시 `DEV_TOOLS=false`(기본값)로 둡니다.** 꺼져 있으면 404입니다.

### 영수증 인식 테스트 페이지 (운영 서버, 테스트 비밀번호)

같은 페이지를 운영 서버에서도 `/receipt-test` 주소로 열 수 있습니다. `RECEIPT_TEST_PASSWORD`를 설정했을 때만 열리고,
비우면(기본값) 404입니다. 페이지에서 비밀번호를 입력해야 인식하며, 틀린 비밀번호는 401이고 OCR을 돌리지 않습니다.

- 비밀번호는 영문·숫자·기호 12자 이상, 공백 없이. 운영 시작 검사가 짧거나 한글이 섞인 값을 거부합니다.
- 맞는 비밀번호는 그 기기의 브라우저에 기억합니다. 틀리면 지웁니다.
- 틀린 요청도 영수증 업로드와 같은 분당 한도(`OCR_REQUESTS_PER_MINUTE`)에 들어갑니다.
- 사진을 여러 장 고르면(최대 100장) 10장씩 나눠 보내서 읽고, 사진마다 탭으로 결과를 볼 수 있습니다. 서버가 바쁘다고 돌려준 사진은 자동으로 다시 보냅니다. 한 장이 실패해도 나머지는 읽습니다. (`/receipt-test/batch`, 개발용은 `/dev/receipt-test/batch`)
- OCR 오류의 자세한 내용은 보여 주지 않습니다(개발용 페이지는 보여 줍니다). 아무것도 저장하지 않는 것은 같습니다.
- Cloud Run에서는 `python3 scripts/cloudrun.py test-password`로 설정합니다. `deploy/CLOUD_RUN.md` 4절을 보세요.

### DB 마이그레이션

- 개발: `AUTO_CREATE_TABLES=true`(기본)면 시작할 때 테이블을 만듭니다.
- 운영: `AUTO_CREATE_TABLES=false`로 두고 `alembic upgrade head`를 실행합니다.
  (compose라면 `docker compose run --rm api alembic upgrade head`)
- 이미 `create_all`로 만든 DB를 Alembic 관리로 넘기려면 한 번 `alembic stamp head`를 실행합니다.
- 모델을 고친 뒤: `alembic revision --autogenerate -m "설명"`

## 3. 설정 (.env)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://fridge:fridge@db:5432/fridge` | compose에서는 compose 파일 값이 우선합니다 |
| `JWT_SECRET` | `change-me` | **운영에서는 반드시 변경.** 인증코드 해시에도 쓰입니다 |
| `ACCESS_TOKEN_MINUTES` / `REFRESH_TOKEN_DAYS` | 30 / 14 | 토큰 유효기간 |
| `EMAIL_BACKEND` | `console` | `console`: 인증 메일을 로그에 출력, `smtp`: 실제 발송 |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASSWORD` `MAIL_FROM` | Gmail 기준 | STARTTLS로 보냅니다 |
| `UPLOAD_DIR` / `MAX_UPLOAD_MB` | `uploads` / 10 | 영수증 이미지 저장 위치와 크기 제한 |
| `OCR_ENGINE` / `OCR_LANG` | `paddle` / `korean` | `none`이면 OCR 없이 빈 초안만 만듭니다 |
| `OCR_DET_MAX_SIDE` | 0 | 글자 위치 검출 해상도(긴 변 px). 0이면 원본 해상도(가장 느림). 아래 "OCR 속도" 참고 |
| `OCR_ENGINES` / `OCR_CPU_THREADS` | 1 / 2 | 사진을 동시에 읽는 OCR 엔진 수 / 엔진 하나가 쓰는 스레드 수. 엔진마다 메모리를 1~2GB 쓰고, 엔진 수 × 스레드 수를 서버 CPU 개수에 맞춘다(Cloud Run은 2 × 4 = 8) |
| `OCR_QUEUE_WAIT_SECONDS` / `OCR_QUEUE_MAX` / `BATCH_SECONDS` | 100 / 40 / 150 | 엔진이 비길 기다리는 최대 초 / 줄에서 기다릴 수 있는 사진 수(넘으면 바로 503) / 사진 여러 장 한 요청이 쓰는 시간(Cloud Run 제한 180초보다 짧게) |
| `OCR_ENABLE_MKLDNN` | `true` | 인텔 CPU 가속(oneDNN). 결과는 같고 10배쯤 빨라지며 메모리도 적게 씁니다. 문제가 생기는 CPU에서만 끕니다 |
| `OCR_ORIENTATION_MODEL` | `PP-LCNet_x1_0_doc_ori` | 사진 방향(0·90·180·270°) 판별 모델. 가로로 찍거나 뒤집어 찍은 사진을 바로 세워 읽습니다. 판별이 틀린 것 같으면(가로 사진이거나 품목을 거의 못 읽었는데 합계도 안 맞으면) 사진을 직접 돌려 다시 읽습니다. 비우면 끕니다 |
| `FOODSAFETY_API_KEY` | (빈 값) | 비우면 바코드 조회를 끄고 카테고리 추정만 합니다 |
| `CORS_ORIGINS` | `*` | 쉼표로 여러 개 |
| `AUTO_CREATE_TABLES` | `true` | 시작할 때 `create_all` |
| `TIMEZONE` | `Asia/Seoul` | "오늘", 구매일, 통계 월 경계의 기준 |
| `DEV_TOOLS` | `false` | `true`면 로그인 없는 영수증 인식 테스트 페이지(`/dev/receipt-test`)를 엽니다. 운영에서는 `false` |
| `RECEIPT_TEST_PASSWORD` | (빈 값) | 설정하면 비밀번호로 보호한 테스트 페이지(`/receipt-test`)를 엽니다. 운영에서도 쓸 수 있습니다 |

`.env`에서는 값 뒤에 `# 주석`을 붙이지 마세요. 주석은 윗줄에 따로 씁니다.
`JWT_SECRET`이 기본값인 채로 `EMAIL_BACKEND=smtp`이면 시작할 때 경고 로그가 남습니다.

## 4. 식품안전나라 API 키 발급

1. [식품안전나라](https://www.foodsafetykorea.go.kr) 회원가입
2. 데이터활용서비스(OpenAPI) → **인증키 신청**(이용신청)
3. 발급된 키를 `.env`의 `FOODSAFETY_API_KEY`에 넣기
4. 확인: 로그인 후 `GET /products/barcode/{EAN-13 바코드}` → `source`가 `api`면 성공, `error`면 키나 네트워크 문제

쓰는 서비스는 **C005 "바코드연계제품정보"** 하나입니다.
공식 문서에도 C005 바코드 연계정보는 **2018년 이후 최신화가 중단**되었다고 안내되어 있습니다.
최신 상품의 조회를 보장하지 않으며, 제품을 못 찾으면 카테고리 추정으로 넘어갑니다.
[C005 공식 문서](https://www.foodsafetykorea.go.kr/api/openApiInfo.do?svc_no=C005)
조회 결과는 30일 캐시되고 "없음"도 캐시됩니다. 네트워크 오류는 캐시하지 않습니다.

## 5. Gmail SMTP 설정

1. Google 계정 → 보안 → **2단계 인증** 켜기
2. <https://myaccount.google.com/apppasswords> 에서 **앱 비밀번호** 만들기 (16자리)
3. `.env`:

```
EMAIL_BACKEND=smtp
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=내주소@gmail.com
SMTP_PASSWORD=앱비밀번호16자리
MAIL_FROM=내주소@gmail.com
```

메일 발송에 실패해도 API는 성공으로 응답하고 서버 로그에만 남습니다. 메일이 안 오면 로그를 확인하세요.

## 6. API 사용 흐름 (curl)

공통 규칙:

- 에러는 모두 `{"detail": "한국어 메시지"}` 형태입니다. 입력값 오류(422)에는 `errors: [{field, message}]`가 더 붙습니다.
- 시각은 UTC ISO 8601(`2026-10-13T05:21:09Z`), 날짜(`purchased_on`, `expiry_date`)는 한국 날짜입니다.
- 냉장고 하위 경로는 멤버가 아니면 404입니다.

```bash
API=http://localhost:8000
```

### 회원가입 3단계

```bash
# 1) 인증코드 발송 (EMAIL_BACKEND=console이면 서버 로그에 code=123456 으로 찍힙니다)
curl -X POST $API/auth/email/send-code -H 'Content-Type: application/json' \
  -d '{"email":"minsu@example.com","purpose":"signup"}'

# 2) 코드 확인 → 15분짜리 1회용 verification_token
curl -X POST $API/auth/email/verify -H 'Content-Type: application/json' \
  -d '{"email":"minsu@example.com","purpose":"signup","code":"123456"}'

# 3) 가입 → access_token, refresh_token. 기본 냉장고 "민수의 냉장고"가 같이 만들어집니다
curl -X POST $API/auth/signup -H 'Content-Type: application/json' \
  -d '{"username":"minsu","email":"minsu@example.com","password":"pass1234","nickname":"민수","verification_token":"<2의 토큰>"}'

TOKEN=<access_token>
curl $API/fridges -H "Authorization: Bearer $TOKEN"     # 냉장고 id 확인
FRIDGE=1
```

아이디 찾기는 `purpose=find_id`, 비밀번호 재설정은 `purpose=reset_password`(+`username`)로 같은 1→2단계를 거친 뒤
`POST /auth/find-id` 또는 `POST /auth/reset-password`를 호출합니다.

### 영수증 업로드 → 확정

```bash
# 업로드 → 초안(pending). draft_items, items_sum, warnings 를 확인 화면에 보여 줍니다
curl -X POST $API/fridges/$FRIDGE/receipts -H "Authorization: Bearer $TOKEN" -F "file=@receipt.jpg"

# 사용자가 고친 품목 목록으로 확정 → 그때 냉장고에 들어갑니다
curl -X POST $API/fridges/$FRIDGE/receipts/1/confirm -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
    "store_name": "이마트 성수점",
    "purchased_on": "2026-10-10",
    "items": [
      {"line_no": 1, "name": "서울우유 1L", "quantity": 1, "amount": 2980, "discount": 500, "expiry_date": "2026-10-20", "include": true},
      {"line_no": 2, "name": "풀무원 두부", "quantity": 2, "amount": 3000, "discount": 0, "include": true},
      {"line_no": 3, "name": "물티슈", "quantity": 1, "amount": 1000, "discount": 0, "include": false}
    ]
  }'
```

- `include=false`인 줄(비식품 등)은 냉장고에는 안 들어가지만 구매 기록에는 남습니다.
- 초안의 품목 객체를 그대로 돌려보내도 됩니다(모르는 필드는 무시).
- **소비기한 출처(`expiry_source`)**: `expiry_date`를 비우거나 초안 값 그대로 보내면 자동 추정(`barcode`/`category`)으로, 다른 날짜를 보내면 `manual`로 저장됩니다. 자동 추정인 줄은 확정할 때의 구매일·카테고리로 다시 계산됩니다. 초안과의 대조는 `line_no`(없으면 이름)로 합니다.
- `expiry_source="barcode"`는 제조일 기준 기한을 구매일에 더한 값이라 실제보다 길 수 있습니다. 앱에서 "포장지를 확인하세요" 안내를 띄우는 용도입니다.
- 같은 영수증(바코드)을 다시 올리면 `409 {"detail": "...", "receipt_id": 기존id}`가 옵니다.
- 초안이 마음에 안 들면 `DELETE /fridges/$FRIDGE/receipts/1`로 지우고 다시 올립니다 (pending만 가능).

### 사진 여러 장을 한 번에 올리기

```bash
curl -X POST $API/fridges/$FRIDGE/receipts/batch -H "Authorization: Bearer $TOKEN" \
  -F "files=@a.jpg" -F "files=@b.jpg" -F "files=@c.jpg"
```

- 한 장 올리는 `POST /receipts`는 그대로입니다. 새 주소는 같은 서버 안의 경로일 뿐이라 서버나 비용은 늘지 않습니다.
- 한 요청에 최대 50장(`MAX_BATCH_FILES`)입니다. 0장이나 51장 이상이면 `400`이고 아무것도 처리하지 않습니다. 이 수는 안전장치일 뿐이고 실제로는 **시간**이 먼저 한도가 됩니다(아래). 더 많이 올릴 때는 앱이 나눠서 여러 번 보내면 됩니다. 테스트 페이지가 그렇게 합니다(10장씩).
- 서버가 보낸 순서대로 저장하고, OCR 엔진이 여러 개(`OCR_ENGINES`)이면 사진 여러 장을 엔진 수만큼 **동시에** 읽습니다. 결과는 보낸 순서로 돌려줍니다. 장마다 따로 저장되어서 한 장이 실패해도 나머지는 초안이 됩니다.
- **한 요청이 쓰는 시간은 `BATCH_SECONDS`(150초)까지입니다.** 다른 사람 사진을 기다리느라 시간이 다 되면, 아직 시작하지 못한 사진은 `503` + `retry_after`로 돌려주고(저장되지 않음) 앱은 그 사진들만 다시 보내면 됩니다. Cloud Run의 요청 제한(180초)에 걸려 응답을 통째로 잃는 일이 없게 하려는 것입니다.
- **여러 사람이 동시에 올려도 거절하지 않고 줄을 세웁니다.** 요청(사람) 단위로 온 순서대로 한 사람씩 읽습니다. 한 사람의 사진을 모두 읽고 나서 다음 사람 차례이고, 엔진이 남으면 다음 사람의 사진도 같이 읽습니다(사진마다 줄을 서면 사람들이 한 장씩 번갈아 읽혀서 모두 늦게 끝납니다). 엔진을 기다리는 사진은 최대 `OCR_QUEUE_WAIT_SECONDS`(100초) 기다리고, 줄에 선 사진이 `OCR_QUEUE_MAX`(40장)를 넘으면 그 사진은 바로 `503` + `retry_after`입니다. 한 장 올리기(`POST /receipts`)도 같은 줄에 섭니다. 앞사람이 사진을 아주 많이 올렸다면 그 시간만큼 기다리게 되므로, 앱은 `503`을 받으면 `retry_after` 뒤에 그 사진만 다시 보내세요.
- 응답은 `{"results": [...], "succeeded": n, "failed": m}`이고 `results`는 보낸 순서입니다. 각 항목:
  `ok`가 true면 `receipt`에 한 장 업로드와 같은 초안이 들어 있고, false면 `status_code`와 `detail`에 사유가 있습니다.
  사유는 한 장 업로드가 돌려줬을 것과 같습니다: `400` 형식·크기, `409` 이미 등록됨(`receipt_id`에 기존 번호), `503` 줄이 가득 참·기다리다 시간 초과·묶음 시간 초과, `429` 한도 초과.
  `503`·`429`는 `retry_after`초 뒤에 **그 장만** 다시 올리면 됩니다.
- 영수증 업로드 한도(`OCR_REQUESTS_PER_MINUTE`)는 사진 한 장을 한 번으로 셉니다.
- Cloud Run은 요청 하나의 크기가 32MiB까지입니다. 앱이 사진을 긴 변 2000px 안팎으로 줄여서 올리면 한 요청에 수십 장도 아래입니다. 줄이지 않은 폰 사진(3~6MB)은 한 요청에 6~10장이 한계라서 10장 안쪽으로 나눠 보내는 게 안전합니다.
- 시간(내 PC, 실제 영수증, 실제 OCR): 12장을 한 요청으로 27초. 4명이 5장씩 20장을 동시에 올리면 사람 순서대로 12초, 23초, 34초, 46초에 끝나고(혼자일 때 5장은 10초 안팎, 평균 29초), 25명이 동시에 한 장씩 올리면 21명은 읽고 4명은 0.6초 만에 `503`입니다. 장 수가 늘면 요청이 오래 걸리니 앱은 진행 중 표시를 보여 주세요.

### 쇼핑몰 주문내역 화면 캡처 (쿠팡 등)

주문내역 화면을 캡처해서 영수증과 **같은 주소**(`POST /receipts`, `POST /receipts/batch`)로 올립니다. 서버가 화면 모양을 알아보고
(가격·수량 줄 `12,900원 4개`와 `배송조회`·`배송·주문관리` 같은 낱말이 함께 있을 때) 영수증과 다르게 읽습니다.

- 카드마다 날짜 → 배송 상태 → 상품 이름 → `가격 · 수량` 순서로 읽습니다. 화면의 가격은 **개당 가격**이라 금액은 `가격 × 수량`입니다.
  (쿠팡 목록에서 4개를 주문한 카드의 결제 금액이 화면 가격의 4배인 것을 확인했습니다. 마켓컬리는 샘플이 없어 같다고 가정합니다.)
- **식품으로 알아본 것만 냉장고에 넣고, 옷·생활용품과 모르는 상품은 기본으로 제외(`include=false`)합니다.** 사용자가 직접 켤 수 있습니다.
- 화면에서 이름이 `…`로 잘려 있으면 `name_cut=true`이고 경고 "상품 이름이 화면에서 잘려 있어요"가 붙습니다. 확정 전에 이름을 고치게 하세요.
- 취소·반품·환불 카드는 건너뜁니다. 한 카드에 상품이 여럿이어도 읽습니다.
- 맞춰 볼 총액이 없어서 `total_amount`는 품목 금액의 합입니다. `store_name`은 `로켓`이 보이면 `쿠팡`, `샛별`·`컬리`가 보이면 `마켓컬리`입니다.
- 한 장에 주문 날짜가 여러 개여도 영수증 하나의 구매일은 **첫 품목 카드의 날짜**입니다. 날짜가 다른 주문은 따로 캡처해서 올리세요.
- 링크(URL)로 주문을 가져오는 방법은 없습니다. 쿠팡의 공개 API는 판매자용이고, 주문내역 페이지는 로그인이 필요해서 서버가 사용자 계정으로 들어가야 하기 때문입니다.
- 이 화면은 이름 자동 보정을 쓰지 않습니다. (자모 하나 차이로 옷 이름 "와이드"가 음료 "에이드"로 바뀌었습니다.)

### 먹음 / 버림

```bash
curl -X POST $API/fridges/$FRIDGE/items/3/consume -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"quantity": 0.5}'      # 반만 먹음
curl -X POST $API/fridges/$FRIDGE/items/3/discard -H "Authorization: Bearer $TOKEN"   # 남은 것 전부 버림
```

### 임박 목록

```bash
curl "$API/fridges/$FRIDGE/items/expiring?days=3" -H "Authorization: Bearer $TOKEN"
# 이미 지난 것도 포함되고 days_left가 음수로 옵니다
```

### 월별 통계·구매 기록

```bash
curl "$API/fridges/$FRIDGE/stats/monthly?year=2026&month=10" -H "Authorization: Bearer $TOKEN"
curl "$API/fridges/$FRIDGE/purchases/summary?group_by=item" -H "Authorization: Bearer $TOKEN"   # 우유: 3번, 4개, 8,940원
```

### 냉장고 참여자 초대

```bash
# 주인이 초대 생성: viewer(보기만) / editor(함께 관리)
curl -X POST $API/fridges/$FRIDGE/share-links -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"permission":"editor","expires_in_days":7}'
# 응답 token을 앱의 /share/{token} 링크로 전달. 상대가 로그인 후 참여한다.
curl -X POST $API/share-links/$SHARE_TOKEN/join -H "Authorization: Bearer $GUEST_TOKEN"
# 초대받은 냉장고와 주인이 관리하는 참여자 목록
curl $API/shared-fridges -H "Authorization: Bearer $GUEST_TOKEN"
curl $API/fridges/$FRIDGE/access -H "Authorization: Bearer $TOKEN"
```

전체 엔드포인트는 `/docs`(Swagger)에서 볼 수 있습니다.

## 7. 영수증 인식의 한계와 팁

잘 찍는 법:

- **평평하게** 펴서, 구겨지거나 말린 곳이 없게
- **밝은 곳에서**, 그림자와 반사 없이
- **영수증만 화면에 꽉 차게**, 기울지 않게 (줄이 기울면 품목과 금액이 다른 줄로 묶입니다)
- 긴 영수증은 글자가 작아지지 않게 나눠 찍기 (긴 변 2000px로 줄여서 인식합니다)
- 인식은 사진 한 장에 10~25초 걸립니다. 빠르게 하려면 `OCR_DET_MAX_SIDE=1280`을 씁니다 (8번 표 참고)

알아 둘 한계:

- 파서는 규칙 기반입니다. 한 줄형(`상품명 단가 수량 금액`)과 2줄형(1줄 상품명, 2줄 코드·단가·수량·금액)을 다루고, 그 밖의 레이아웃은 품목이 빠지거나 숫자가 어긋날 수 있습니다.
- 품목 합과 영수증 총액이 1% 넘게 다르면 `warnings`에 "일부 품목을 못 읽었을 수 있어요"가 붙습니다.
- 숫자가 2개뿐인 줄(`계란 30 6,980`)은 앞의 수가 99 이하면 수량으로 봅니다. 규격 숫자(30구)와 헷갈릴 수 있습니다.
- 소비기한은 추정치입니다. 카테고리 일수는 [app/services/expiry.py](app/services/expiry.py)의 `CATEGORIES` 사전 하나만 고치면 됩니다.
- 오래 흐려진 감열지 영수증은 사람이 읽기 어려우면 OCR도 못 읽습니다.

## 8. 확인 필요 (검증하지 못한 것)

| 항목 | 지금 가정한 것 | 확인 방법 |
|---|---|---|
| C005 응답 필드명 | `PRDLST_NM`(제품명), `POG_DAYCNT`(소비기한 문구), `PRDLST_DCNM`(식품유형), `BSSH_NM`(제조사), `BAR_CD` | 공식 문서와 공개 샘플에서 확인. 발급 키로 실제 상품 조회는 별도 확인 필요 |
| C005 결과 코드 | `INFO-000` 정상, `INFO-200` 데이터 없음, 그 외는 오류로 처리 | 공식 문서에서 확인. 발급 키의 권한과 호출 한도는 별도 확인 필요 |
| C005 응답 구조 | `{"C005": {"total_count": "1", "row": [...], "RESULT": {"CODE": "...", "MSG": "..."}}}` | 공개 샘플 JSON에서 확인 |
| 실제 영수증 사진 인식 | 한글 폰트로 그린 합성 영수증 이미지로만 확인 (품목·합계·바코드 인식됨) | 실제 사진으로 업로드 → 초안 확인 |
| OCR 속도와 `OCR_DET_MAX_SIDE` | 개발 PC의 Docker(CPU)에서 휴대폰 사진 크기(긴 변 2000px) 한 장에 기본값 0은 17~24초, 1280은 7~11초, 960은 6~8초. 합성 영수증 2장에서는 세 설정 모두 품목 인식 결과가 같았음. 서버 시작 후 첫 장은 모델 로딩으로 10~15초 더 걸림 | 실제 영수증 사진 여러 장으로 0과 1280의 인식 결과를 비교한 뒤 운영 값을 정하기. 작은 서버에서는 더 느림 |
| 기운 사진 (줄 묶기 기울기 보정) | 실제 영수증 6장을 ±4°, ±8° 기울인 24건: 보정 전 3건, 보정 후 21건이 똑바른 사진과 금액·총액이 같음. 글자 상자 꼭짓점으로 잰 기울기 중앙값만큼 좌표를 돌려서 줄을 묶음(1° 미만은 보정 안 함). 다시 읽지 않으므로 시간이 늘지 않음 | 실제로 기울게 찍은 사진에서 확인 |
| 시험했지만 넣지 않은 것 | 검출 임계값 낮추기(0.3/0.6 → 0.2/0.5): 변화 없음, 어두운 사진 4/6 → 3/6. 적응형 이진화: 그대로 찍은 사진 3/6, 어두운 사진 4/6(CLAHE 6/6보다 나쁨). 미디언 필터+CLAHE: 어두운 사진 4/6. 합계가 안 맞을 때 원본 해상도로 다시 검출: 결과 변화 없이 느려짐. 원근 보정: 위쪽이 25% 좁게 찍힌 사진도 이미 6/6이라 효과를 확인할 수 없음 | 실제로 실패하는 사진이 생기면 다시 시험 |
| 전처리 (흑백·그림자 제거·히스토그램 평활화) | 실제 영수증 6장을 그대로·그림자·어둡게 시험: 항상 적용하면 멀쩡한 사진이 나빠짐(그대로 찍은 사진 6장 중 2~4장만 같음). 어두운 사진에서는 CLAHE가 3장 → 6장. 그래서 첫 결과의 합계가 맞지 않을 때만 CLAHE로 한 번 더 읽고, 합계가 맞으면 그 결과를 씀(어두운 사진 3/6 → 4/6, 나머지는 그대로) | 실제로 어둡거나 흐리게 찍힌 사진에서 확인 |
| 돌아간 사진 (`OCR_ORIENTATION_MODEL`) | 실제 영수증 6장을 90·180·270° 돌린 18건: 방향 판별이 없으면 0건, 판별만 켜면 15건, 판별이 틀렸을 때 직접 돌려 다시 읽기까지 하면 18건 모두 똑바른 사진과 금액·총액이 같음. 똑바른 사진은 결과와 시간이 그대로이고, 다시 읽은 경우 2~8초 더 걸림 | 실제로 가로로 찍은 사진(그림자·기울기 포함)에서 확인 |
| `OCR_ENABLE_MKLDNN` (oneDNN) | CPU 2개·메모리 8GB·1280 조건, 실제 영수증 6장: 끄면 한 장 18~29초·최대 6.9GB, 켜면 1.4~2.8초·최대 1.9GB. OCR 원문 줄이 6장 모두 한 줄도 다르지 않았음. 크기가 다른 사진 90장을 연속으로 읽어도 메모리가 1.5~1.6GB로 유지됨(최대 2.2GB) | Cloud Run CPU에서의 실제 시간과 메모리 |
| 읽기 보정 (실제 사진 6장을 테스트 페이지처럼 줄여서 읽어 본 뒤 고친 것) | 단가·수량·할인·금액 네 칸이 있는 영수증에서 할인 칸의 0이 단가 0원이 되던 것, 사진 배경 글자 때문에 위 다섯 줄에 가게 이름이 안 들어가던 것(사업자번호 줄을 15줄까지 찾음), OCR이 한 글자를 통째로 틀린 네 글자 이상 상품명(코카율라 → 코카콜라). 고친 뒤 6장을 다시 읽으면 위 세 가지만 바뀌고 나머지 품목·금액은 그대로. 고치지 않은 것: 버거킹 주문서는 사진에 합계 줄이 없어 총액을 읽을 수 없고, 지오마트 품목 합계 72,908원은 영수증 합계 72,900원이 10원 단위 절사라서 맞는 값 | 사진 6장뿐이라 더 고치려면 다른 영수증이 더 필요하다 |
| 사진을 병렬로 읽기 (OCR 엔진 여러 개를 동시에) | 실제 영수증, 같은 CPU 예산에서 엔진 1개와 비교. CPU 2개 12장: 엔진 1개 27.2초, 엔진 2개 24.4초(10% 빠름). CPU 4개 16장: 엔진 1개 23.5초, 4개 18.0초(23%). CPU 8개 24장: 엔진 1개(스레드 8) 29.2초, 엔진 4개(스레드 2) 17.8초(39% 빠름, 메모리는 엔진마다 약 2GB). 구현 후 8개 CPU 컨테이너에서 5장 한 번: 엔진 1개 8.2초 → 엔진 4개 6.0초, 4명이 5장씩 동시에: 마지막 사람 29.1초 → 16.9초(사람 순서 7.9·10.6·14.6·16.9초), 읽은 내용은 한 장씩 읽을 때와 25장 모두 같음, 메모리 최대 4.5GB | CPU 2개에서는 이득이 거의 없어 엔진 1개로 둔다. CPU가 많을 때(Cloud Run 8개) 엔진 2개 × 스레드 4개로 쓴다. 엔진 4개 × 스레드 2개는 같은 조건에서 5장이 거의 같은 시간(11.1초 vs 10.8초)이고 한 장씩 읽는 시간만 1.6배 길어서(5.0초 vs 3.2초) 쓰지 않는다. 실제 Cloud Run에서도 4 × 2로 5장 20.8초, 사진 한 장당 OCR 11초가 나왔다. 혼자 한 번에 5장을 올리면 엔진 4개가 두 번에 나눠 읽어서(4+1) 기대한 만큼 줄지는 않는다. 실제 Cloud Run(8 CPU, 2 × 4, 영수증 5장 한 번에 / 한 장만): 5장 22.1초(사진마다 서버 처리 7.1·7.3·13.4·18.1·13.7초, 뒤쪽은 엔진이 비길 기다린 시간 포함), 한 장만 7.2초(그중 OCR 7.1초). 엔진 1개·CPU 4개였을 때는 5장 31~33초, 한 장 6.9~8.7초 |

```
http://openapi.foodsafetykorea.go.kr/api/{KEY}/C005/json/1/5/BAR_CD=8801234567890
```

필드명이 다르면 [app/services/foodsafety.py](app/services/foodsafety.py)의 `parse_c005`만 고치면 됩니다.
파싱은 키가 없으면 `None`이 되도록 방어적으로 짜여 있어, 필드명이 틀려도 서버가 죽지 않고 카테고리 추정으로 넘어갑니다.

## 냉장고 참여자 초대 · 공개 프로필 · 커뮤니티

냉장고 주인이 초대 링크로 보기 전용 또는 함께 관리 참여자를 추가합니다. 친구 기능은 제거했으며, 공개 범위는 참여자만/전체 공개입니다. API와 이전 데이터 처리 방식은 [docs/community.md](docs/community.md)를 참고하세요.

레시피에 별점·후기를 남기는 기능과, 처리할 변경 요청·내 글에 달린 댓글을 알려 주는 알림 아이콘용 API는 [docs/recipe-reviews-notifications.md](docs/recipe-reviews-notifications.md)에 있습니다.

**API 전체 목록**은 [docs/api.html](docs/api.html)에 있습니다(경로, 요청·응답 칸, 로그인 필요 여부). 코드에서 자동으로 만든 문서라서 저장소를 받은 뒤 파일을 브라우저로 열어 보세요(GitHub 화면에서는 HTML이 그려지지 않습니다). 운영 서버의 `/docs`는 꺼 두었습니다. API를 고친 뒤에는 `python -m scripts.export_api_docs`로 다시 만듭니다. 원본 명세는 [docs/openapi.json](docs/openapi.json)입니다.

## 9. 향후 확장 (이번 범위 아님)

- **푸시 알림(FCM)**: 지금은 임박 목록 조회 API만 있습니다. 매일 아침 임박 품목을 푸시하는 스케줄러를 붙일 수 있습니다.
- **VLM 엔진 교체**: `OcrEngine` Protocol(`recognize(image_path) -> list[OcrBox]`)만 지키면 로컬 VLM(Ollama의 Qwen3-VL 등)으로 바꿀 수 있습니다.
- **S3 이미지 저장**: 지금은 로컬 볼륨(`uploads/{fridge_id}/{uuid}.jpg`)에 저장합니다.
- **레시피 추천**: 임박한 재료로 만들 수 있는 요리 추천.
- 소셜 로그인, 관리자 페이지.

## 프로젝트 구조

```
app/
├── main.py  config.py  database.py  timeutil.py  models.py  schemas.py
├── security.py  deps.py  errors.py
├── routers/   auth  users  fridges  items  receipts  products  stats  dev(테스트 페이지)
├── static/    receipt_test.html
└── services/
    ├── email.py  verification.py  barcode.py  foodsafety.py
    ├── expiry.py            # 카테고리 사전 + 소비기한 추정
    ├── size.py              # 상품명에서 용량·중량·개수 추출
    ├── receipt_parser.py    # 영수증 파서 (순수 함수)
    └── ocr/  base.py  paddle.py
alembic/  tests/  Dockerfile  docker-compose.yml
```

### 로컬에서 웹과 함께 쓰기

서버 저장소에서 `docker compose up --build -d`를 실행하면 API가 `http://localhost:8000`에서 열린다.
앱 저장소의 `EXPO_PUBLIC_API_URL`을 `http://localhost:8000`으로 두고 `npm run web`을 실행한다.
실제 휴대폰에서는 `localhost` 대신 개발 PC의 IP를 API 주소에 설정한다.
