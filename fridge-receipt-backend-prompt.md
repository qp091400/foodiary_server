# 냉장고 영수증 앱 백엔드 구현 요청

이 문서는 영수증 기반 식품 관리 앱의 **백엔드 전체**를 구현해 달라는 요청서야. 기술 선택과 기능 범위는 이미 확정됐으니 그대로 따라 줘. 애매한 부분은 "기본값" 항목대로 하고, 끝나면 무엇을 어떻게 결정했는지 요약해 줘.

---

## 1. 앱 개요

사용자가 마트 영수증 사진을 올리면:

1. 영수증에서 구매 품목(이름, 수량, 단가, 금액)을 자동으로 뽑는다.
2. 품목마다 **예상 소비기한을 자동으로 채운다.** 사용자는 확인 화면에서 수정할 수 있다.
3. 확정하면 품목이 "냉장고"에 들어가고, 무엇을 얼마치 샀는지 구매 기록이 남는다.
4. 다 먹었거나 버린 것을 처리하면, 월별 지출과 버린 금액 통계를 볼 수 있다.

그 외 기능:
- 회원가입, 로그인, 아이디 찾기, 비밀번호 찾기 (이메일 인증코드 방식)
- 냉장고를 가족이나 룸메이트와 **공유**하는 기능 (초대코드)

---

## 2. 확정된 기술 결정 (변경하지 말 것)

| 항목 | 결정 |
|---|---|
| 언어/프레임워크 | Python 3.11, **FastAPI** |
| DB | **PostgreSQL** (SQLAlchemy 2.0 ORM, `Mapped` 타입 문법, 드라이버 `psycopg` v3) |
| 마이그레이션 | Alembic (초기 마이그레이션 포함). 개발 편의를 위해 `AUTO_CREATE_TABLES=true`면 시작할 때 `create_all`도 가능하게 |
| 인증 | JWT. 액세스 토큰 30분, 리프레시 토큰 14일. 비밀번호는 bcrypt |
| 아이디 체계 | **별도 아이디 + 이메일** (아이디로 로그인, 이메일은 인증/찾기용) |
| 아이디·비번 찾기 | **이메일 인증코드** (6자리) |
| 영수증 OCR | **로컬 모델 PaddleOCR (PP-OCRv5, `lang="korean"`)** + 직접 짠 규칙 기반 파서. 유료 OCR API 사용 금지 |
| 냉장고 | **공유 가능** (냉장고 1개에 여러 사용자, 초대코드로 참여) |
| 소비기한 알림 | **임박 목록 조회 API만** 만든다. 푸시(FCM)는 이번 범위가 아님 |
| 기록 범위 | **구매 기록 + 소비/폐기 처리** → 월별 지출·낭비 통계 |
| 배포 | Docker + docker-compose (앱 + PostgreSQL). 나중에 클라우드에 올릴 예정이라 CPU 서버에서 돌아가야 함 |

---

## 3. 꼭 알고 시작해야 할 사실 (설계에 반영할 것)

1. **영수증 하단 바코드에는 품목 정보가 없다.**
   - 영수증 바코드는 보통 거래번호(영수증 번호)만 담고 있다. 품목 목록은 매장 POS 서버에만 있어서 외부에서 조회할 수 없다.
   - 그래서 영수증 바코드는 **같은 영수증 중복 등록 방지 키**로만 쓴다. 이미지에서 디코딩할 때는 `zxing-cpp` 파이썬 패키지를 쓴다 (시스템 라이브러리 불필요).
   - 바코드가 안 읽혀도 업로드는 정상 진행해야 한다.
2. **상품 바코드(EAN-13)로 소비기한을 조회할 수 있다.**
   - 식품안전나라 오픈API **C005 "바코드연계제품정보"**를 쓴다. API 키는 무료로 발급받는다.
   - 요청 형식: `http://openapi.foodsafetykorea.go.kr/api/{KEY}/C005/json/1/5/BAR_CD={바코드}`
   - 내가 알고 있는 응답 필드명: `PRDLST_NM`(제품명), `POG_DAYCNT`(소비기한 문구, 예: "제조일로부터 12개월"), `PRDLST_DCNM`(식품유형), `BSSH_NM`(제조사), `BAR_CD`.
   - **이 필드명은 공식 문서로 검증하지 못했다.** 파싱 코드를 방어적으로 짜고(키가 없으면 None), 실제 응답 예시를 README에 "확인 필요"로 표시해 줘.
   - 참고로 다른 API인 I2570 "유통바코드"는 2018년 이후 갱신이 중단됐으니 쓰지 않는다.
   - 일부 마트 영수증에는 품목마다 상품코드(13자리)가 찍혀 있다. 파서가 이걸 뽑으면 자동으로 C005를 조회한다.
3. **OCR은 100% 정확하지 않다.** 업로드 직후 바로 냉장고에 넣지 말고, 반드시 아래 2단계를 거친다.
   - 업로드하면 **초안(pending)**을 돌려준다.
   - 사용자가 수정 후 **확정(confirm)**하면 그때 냉장고에 넣는다.
4. `POG_DAYCNT`는 **제조일 기준**이다. 구매일에 더하면 실제보다 길게 나올 수 있으므로 `expiry_source="barcode"`로 표시하고, 앱에서 "포장지 확인" 안내를 띄울 수 있게 한다.

---

## 4. 프로젝트 구조

```
fridge-backend/
├── app/
│   ├── main.py              # FastAPI 앱, 라우터 등록, CORS, lifespan
│   ├── config.py            # pydantic-settings 기반 설정 (.env)
│   ├── database.py          # engine, SessionLocal, Base, get_db
│   ├── timeutil.py          # utcnow()(naive UTC), today()(Asia/Seoul 기준 날짜)
│   ├── models.py
│   ├── schemas.py
│   ├── security.py          # bcrypt, JWT, 인증코드/초대코드 생성
│   ├── deps.py              # get_current_user, get_fridge_access, require_owner
│   ├── routers/
│   │   ├── auth.py  users.py  fridges.py  items.py
│   │   ├── receipts.py  products.py  stats.py
│   └── services/
│       ├── email.py         # console / smtp 백엔드
│       ├── verification.py  # 인증코드 발급·검증·토큰 1회 사용 처리
│       ├── ocr/
│       │   ├── base.py      # OcrBox dataclass, OcrEngine Protocol
│       │   └── paddle.py    # PaddleOcrEngine (지연 로딩, 싱글톤, 스레드 락)
│       ├── receipt_parser.py
│       ├── barcode.py       # 영수증 이미지에서 바코드 디코딩 (zxing-cpp)
│       ├── expiry.py        # 카테고리 분류 + 소비기한 추정
│       └── foodsafety.py    # C005 조회 + ProductCache
├── alembic/  alembic.ini
├── tests/
├── requirements.txt         # 서버·테스트용 (paddle 제외)
├── requirements-ocr.txt     # paddleocr, paddlepaddle(CPU)
├── Dockerfile  docker-compose.yml  .env.example  README.md
```

- 시간은 DB에 **timezone 없는 UTC**로 저장한다.
- "오늘", 구매일, 통계 월 경계는 **Asia/Seoul** 기준으로 계산한다.
- 슬림 이미지에서 `zoneinfo`가 동작하도록 `tzdata`를 requirements에 넣는다.

---

## 5. 데이터 모델

| 테이블 | 주요 컬럼 | 비고 |
|---|---|---|
| `users` | id, username(unique, 4~20자 `[a-z0-9_]`), email(unique, 소문자 저장), password_hash, nickname, **token_version**, created_at | 비밀번호 변경·재설정 시 token_version +1 → 기존 리프레시 토큰 무효화 |
| `email_verifications` | id, email, purpose(`signup`/`find_id`/`reset_password`), code_hash, expires_at, attempts, verified_at, consumed_at, created_at | 코드는 평문 저장 금지 → HMAC-SHA256(secret, `email:purpose:code`) |
| `fridges` | id, name, owner_id, created_at | |
| `fridge_members` | id, fridge_id, user_id, role(`owner`/`member`), joined_at | unique(fridge_id, user_id) |
| `fridge_invites` | id, fridge_id, code(8자, 0/O/1/I/L 제외), created_by, expires_at(7일) | |
| `receipts` | id, fridge_id, uploaded_by, status(`pending`/`confirmed`), store_name, purchased_on, total_amount, receipt_barcode, image_path, raw_lines(JSON), draft_items(JSON), created_at, confirmed_at | **unique(fridge_id, receipt_barcode)** — NULL은 여러 개 허용 |
| `receipt_items` | id, receipt_id, line_no, name, barcode, quantity, unit_price, amount, discount, category, included, food_item_id | 확정 시 **모든 줄** 저장 (비식품 포함 = 구매 기록) |
| `food_items` | id, fridge_id, receipt_id, name, category, storage(`fridge`/`freezer`/`room`), barcode, quantity, remaining_quantity, unit_price, total_price(할인 반영 실결제액), purchased_on, expiry_date, expiry_source(`barcode`/`category`/`manual`), status(`active`/`consumed`/`discarded`), memo, added_by, created_at, updated_at, finished_at | 수량은 float (반 개 소비 가능) |
| `consumption_logs` | id, fridge_id, food_item_id(SET NULL), item_name, category, user_id, action(`consume`/`discard`), quantity, value(원), **logged_on(KST 날짜)**, created_at | 식품을 지워도 통계가 남도록 이름·카테고리 복사 |
| `product_cache` | barcode(PK), found, name, food_type, manufacturer, shelf_life_text, shelf_life_days, fetched_at | C005 결과 30일 캐시. "없음"도 캐시하되, 네트워크 오류는 캐시하지 않음 |

JSON 컬럼은 `sqlalchemy.JSON`을 쓴다 (테스트의 SQLite와 호환되게).

---

## 6. API 명세

모든 에러 메시지는 **한국어**로 쓴다. 냉장고 하위 경로는 멤버가 아니면 **404**를 준다 (존재 여부 비노출).

### 6.1 인증 `/auth`

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/auth/check-username?username=` | 아이디 형식·중복 확인 → `{available, reason}` |
| POST | `/auth/email/send-code` | `{email, purpose, username?}` |
| POST | `/auth/email/verify` | `{email, purpose, code}` → `{verification_token, expires_in}` |
| POST | `/auth/signup` | `{username, email, password, nickname, verification_token}` → 토큰 + 유저. **가입 시 기본 냉장고 "{닉네임}의 냉장고" 자동 생성** (owner) |
| POST | `/auth/login` | `{username, password}` → `{access_token, refresh_token, token_type, user}` |
| POST | `/auth/refresh` | `{refresh_token}` → 새 토큰 쌍. `ver != token_version`이면 401 |
| POST | `/auth/find-id` | `{verification_token}`(purpose=find_id) → `{username, created_at}` |
| POST | `/auth/reset-password` | `{username, verification_token, new_password}` → token_version +1 |

인증 규칙:

**인증코드 발급과 검증**
- 6자리, 유효 5분, 오입력 5회면 무효.
- 같은 email+purpose로는 60초 안에 재발송 금지 (429 + 남은 초 안내).
- 확인에 성공하면 15분짜리 **verification JWT**를 준다. 페이로드는 `vid`, `email`, `purpose`, `type="verify"`.
- 이 토큰은 **1회용**이다. 사용하면 해당 행의 `consumed_at`을 기록하고, 실제 작업과 같은 트랜잭션에서 commit한다.

**계정 존재 여부 노출 방지**
- `signup`: 이미 가입된 이메일이면 409.
- `find_id`, `reset_password`: 계정이 없어도 **항상 같은 응답**을 준다. ("입력한 정보와 일치하는 계정이 있다면 인증코드가 발송됩니다.") 실제 발송은 계정이 있을 때만 한다.
- `reset_password` 코드 발송은 username과 email이 같은 계정일 때만 한다.

**입력값 규칙**
- 비밀번호: 8~64자, 영문과 숫자를 모두 포함.
- 아이디: 입력값을 소문자로 정규화.

**JWT 토큰**
- `type` 클레임으로 `access` / `refresh` / `verify`를 구분한다.
- 다른 종류의 토큰이 들어오면 거부한다.

**메일 발송**
- `BackgroundTasks`로 보낸다.
- `EMAIL_BACKEND=console`이면 로그에 출력하고 모듈 전역 `OUTBOX` 리스트에 쌓는다 (테스트에서 코드 꺼내기용).
- `smtp`면 STARTTLS로 보낸다 (Gmail 앱 비밀번호 사용 가정). 발송 실패는 로그만 남기고 API는 성공 처리한다.

### 6.2 사용자 `/users`

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/users/me` | 내 정보 |
| PATCH | `/users/me` | 닉네임 변경 |
| POST | `/users/me/password` | `{current_password, new_password}` → token_version +1 |

### 6.3 냉장고 `/fridges`

| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| GET | `/fridges` | 로그인 | 내가 속한 냉장고 목록 (my_role, member_count) |
| POST | `/fridges` | 로그인 | 새 냉장고 (생성자 = owner) |
| GET | `/fridges/{id}` | 멤버 | 상세 + 멤버 목록 |
| PATCH | `/fridges/{id}` | owner | 이름 변경 |
| DELETE | `/fridges/{id}` | owner | 삭제 (식품·영수증 cascade) |
| POST | `/fridges/{id}/invites` | 멤버 | 초대코드 생성 → `{code, expires_at}` |
| POST | `/fridges/join` | 로그인 | `{code}`로 참여. 이미 멤버면 409, 만료·없음이면 404 |
| DELETE | `/fridges/{id}/members/{user_id}` | owner(타인) / 본인 | 내보내기·나가기. **owner 본인은 나가기 불가** (삭제하라고 안내) |

### 6.4 식품 `/fridges/{id}/items`

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/items?status=active\|consumed\|discarded\|all&category=&q=&sort=expiry\|recent` | 목록. 기본은 active, 소비기한 임박순 (NULL은 맨 뒤) |
| GET | `/items/expiring?days=3` | **임박 목록**: active이면서 `expiry_date <= 오늘+days`. 이미 지난 것도 포함하고 `days_left`는 음수로 준다 |
| POST | `/items` | 수동 추가. category·expiry_date를 비우면 자동 추정. barcode가 있으면 C005로 우선 조회 |
| GET | `/items/{item_id}` | 상세 |
| PATCH | `/items/{item_id}` | 이름·카테고리·보관·수량·남은수량·가격·소비기한·메모 수정. 소비기한을 직접 바꾸면 `expiry_source="manual"` |
| DELETE | `/items/{item_id}` | 잘못 넣은 항목 삭제 (통계에서도 빠짐) |
| POST | `/items/{item_id}/consume` | `{quantity?}` 비우면 남은 양 전부. 로그 기록 |
| POST | `/items/{item_id}/discard` | 위와 같고 action=discard |

모든 식품 응답에는 `category_label`(한글)과 `days_left`(오늘 기준, 소비기한 없으면 null)를 포함한다.

먹음/버림 처리 규칙:
- `quantity`가 남은 양보다 크면 400. active가 아닌 항목이면 400.
- 기록 금액(`value`) = 실제 단가 × 처리 수량 (반올림). 실제 단가는 `total_price/quantity`이고, `total_price`가 없으면 `unit_price`, 둘 다 없으면 0.
- 처리 후 `remaining_quantity`가 0이 되면 status를 `consumed` 또는 `discarded`로 바꾸고 `finished_at`을 기록한다.

### 6.5 영수증 `/fridges/{id}/receipts`

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/receipts` | multipart `file` (jpg/png/webp, 최대 10MB) → **초안(pending)** 반환 |
| GET | `/receipts?status=` | 목록 |
| GET | `/receipts/{rid}` | 상세 (초안 + 원문 줄) |
| POST | `/receipts/{rid}/confirm` | 사용자가 수정한 품목 목록으로 확정 |
| DELETE | `/receipts/{rid}` | pending만 삭제 가능 (다시 올릴 수 있게) |

업로드 처리 순서:
1. 확장자와 크기를 검사한다.
2. PIL로 열어 `ImageOps.exif_transpose`(폰 사진 회전 보정)를 적용하고, RGB로 바꾸고, 긴 변 2000px로 리사이즈한다.
3. `uploads/{fridge_id}/{uuid}.jpg`로 저장한다.
4. 영수증 바코드를 디코딩한다. 같은 냉장고에 같은 바코드가 있으면 **409와 기존 receipt_id**를 돌려준다. 이때 방금 저장한 파일은 지운다.
5. OCR → 파서 → 품목별로 카테고리, 소비기한, C005 조회를 수행한다 (품목 코드가 있고 API 키가 설정된 경우).
6. `draft_items`를 저장하고 반환한다.

초안 품목(`DraftItem`) 필드:
`line_no, name, quantity, unit_price, amount, discount, barcode, category, category_label, storage, is_food, include, expiry_date, expiry_source, product_name, confidence`

- 비식품은 `is_food=false`, `include=false`로 둔다.
- 응답에 `items_sum`(품목 실결제 합)과 `warnings`를 포함한다.
  - 합계와 영수증 총액이 ±1% 넘게 다르면 "일부 품목을 못 읽었을 수 있어요"를 넣는다.
  - 인식된 품목이 0개면 "품목을 찾지 못했어요. 직접 추가해 주세요"를 넣는다.

확정 요청 형식:
`{store_name?, purchased_on?, items:[{name, quantity, unit_price?, amount, discount, barcode?, category?, storage?, expiry_date?, include}]}`

확정 처리 규칙:
- pending이 아니면 409.
- 모든 줄을 `receipt_items`로 저장한다.
- `include=true`인 줄만 `food_items`로 만든다.
  - `total_price = amount - discount`
  - `unit_price`가 비어 있으면 `total_price / quantity`
  - `expiry_date`가 비어 있으면 자동 추정한다. 구매일은 `purchased_on` → 영수증 인식 날짜 → 오늘 순으로 정한다.
- 응답: `{receipt_id, created_items, skipped}`

### 6.6 바코드·카테고리

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/products/barcode/{code}` | 8~14자리 숫자만 허용. 응답: `{found, name, food_type, manufacturer, shelf_life_text, shelf_life_days, suggested_category, suggested_expiry_date, source}`. `source`는 `cache` / `api` / `disabled`(키 없음) / `error` 중 하나 |
| GET | `/categories` | 카테고리 목록 `{key, label, storage, default_days}` (앱의 선택 메뉴용) |

### 6.7 구매 기록·통계 `/fridges/{id}`

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/purchases?start=&end=&q=&category=` | 산 식품 목록 (구매일 내림차순, 매장명 포함) |
| GET | `/purchases/summary?start=&end=&group_by=item\|category` | "우유: 3번, 4개, 8,940원" 형태로 집계. 기본 기간은 이번 달. `group_by=item`이면 이름을 공백 제거·소문자로 정규화해서 묶는다 |
| GET | `/stats/monthly?year=&month=` | 아래 항목 반환 (기본값은 이번 달) |

`/stats/monthly` 반환 항목:
- `food_spent`: 식품 지출 (food_items.total_price 합)
- `non_food_spent`: 비식품 지출 (영수증 줄 중 included=false의 amount−discount 합)
- `receipt_count`, `item_count`
- `spent_by_category`
- `consumed_value`, `discarded_value` (`consumption_logs.logged_on` 기준)
- `waste_rate` = 버린 금액 / (먹은 금액 + 버린 금액). 둘 다 0이면 0
- `top_discarded`: 많이 버린 카테고리 상위 5개

---

## 7. 영수증 파서 (`receipt_parser.py`): 이 프로젝트의 핵심

PaddleOCR이 주는 것은 `[(text, score, x0, y0, x1, y1)]` 박스 목록뿐이다. 파서는 **순수 함수**로 만들어 OCR 없이 테스트할 수 있게 한다.

```python
@dataclass
class OcrBox: text: str; score: float; x0: float; y0: float; x1: float; y1: float

@dataclass
class ParsedItem: line_no: int; name: str; quantity: float; unit_price: int | None
                  amount: int; discount: int; barcode: str | None; confidence: float

@dataclass
class ParsedReceipt: store_name: str | None; purchased_on: date | None
                     total_amount: int | None; items: list[ParsedItem]; lines: list[str]

def parse_receipt(boxes: list[OcrBox]) -> ParsedReceipt
```

### 7.1 줄 묶기
- 박스를 y 중심값 순으로 정렬한다.
- 현재 줄의 평균 y 중심과의 차이가 **박스 높이 중앙값 × 0.5 이하**면 같은 줄로 묶는다.
- 줄 안의 박스는 x0 순으로 정렬해서 공백으로 이어 붙인다.
- 줄 confidence는 박스 score 평균으로 한다.

### 7.2 정규화
- 전각 문자를 반각으로 바꾼다.
- `2, 980`처럼 OCR이 쉼표 뒤에 넣은 공백을 제거한다.
- 줄 앞의 순번(`001`, `01`)과 면세·행사 표시(`*`, `#`, `★`, `(면세)`, `P`)를 제거한다.

### 7.3 품목 구간 찾기
- **시작**: `상품명|품명|품\s*목` 이 들어간 헤더 줄의 다음 줄. 헤더가 없으면 첫 줄부터.
- **끝**: 시작 이후 처음으로 아래 패턴이 나오는 줄의 앞.
  `합\s*계|총\s*액|총\s*금액|과세\s*물품|면세\s*물품|부\s*가\s*세|받을\s*금액|결제\s*금액|판매\s*총액|공급가|카드\s*승인|신용\s*카드|총\s*구매`

### 7.4 줄 해석

**토큰 분류**
- 8~14자리이고 쉼표 없는 숫자는 상품코드로 본다.
- `1,234` 또는 `-500` 같은 형태는 금액 숫자다.
- 줄 끝에서부터 숫자 토큰을 모으고, 나머지는 상품명으로 본다.

**줄 유형별 처리**
- (a) **상품명 + 숫자**: 한 줄 품목이다.
- (b) **상품명만**: 대기 상태로 두고, 다음 줄이 숫자(+코드)만 있으면 합친다. 2줄형 영수증(1줄 상품명, 2줄 코드·단가·수량·금액)을 위한 처리다.
- (c) **코드 + 숫자만**: 대기 중인 상품명에 붙인다.
- (d) **할인 줄**: `할인|행사|쿠폰|D/?C|에누리` 가 있거나 음수 금액인 줄. 직전 품목의 `discount`에 절대값을 더한다.

**숫자 배정**
- 숫자 3개 이상: 끝의 세 개를 (단가, 수량, 금액)으로 본다.
  - `단가×수량==금액`인지 검증한다.
  - 안 맞으면 99 이하인 수를 수량으로 보고, 금액은 마지막 수로 둔다.
- 숫자 2개 (a, b):
  - `a≤99`이면 (수량, 금액)
  - `a==b`이면 (단가, 수량 1, 금액)
  - `b % a == 0`이고 `b//a ≤ 99`이면 (단가, 수량 b//a, 금액)
  - 그 외에는 (단가 a, 수량 1, 금액 b)
- 숫자 1개: 금액으로 보고 수량은 1. 10 미만이면 수량으로 보고 다음 줄의 금액을 기다린다.

**버릴 줄**
- 상품명이 비었거나 1글자 이하인 줄.
- 헤더 단어(상품명/단가/수량/금액)만 있는 줄.
- 숫자 없이 `영수증|사업자|대표|TEL|전화|주소|POS|계산원|캐셔|교환|환불`만 있는 줄.

### 7.5 메타 정보
- **매장명**: 상단 5줄 중 `마트|편의점|GS25|CU|세븐일레븐|이마트|홈플러스|롯데마트|코스트코|하나로|노브랜드|슈퍼|마켓|점$` 이 들어간 줄을 우선한다. 없으면 한글 2자 이상인 첫 줄.
- **구매일**: `(20\d{2})[-./년\s]*(\d{1,2})[-./월\s]*(\d{1,2})`. 잘못된 날짜는 무시한다.
- **총액**: `받을금액|결제금액` 줄을 `합계|총액|판매총액` 줄보다 우선하고, 그 줄의 마지막 숫자를 쓴다.

### 7.6 테스트 픽스처 (필수)
가짜 OcrBox로 아래 4가지 레이아웃을 만들어 테스트한다.
1. 한 줄형: `서울우유 1L  2,980  1  2,980`
2. 2줄형: `001 풀무원 두부` 다음 줄 `8801114123456  1,500  2  3,000`
3. 할인 줄: `행사할인  -500`
4. 비식품이 섞인 영수증: 종량제봉투, 물티슈

그리고 각 테스트에서 매장명, 날짜, 총액, 품목 수, 할인 반영이 맞는지 확인한다.

---

## 8. 카테고리 분류와 소비기한 추정 (`expiry.py`)

### 8.1 매칭 규칙 (중요)

1. **키워드가 끝나는 위치가 가장 뒤인 것을 고른다.** 한국어 상품명은 핵심 명사가 뒤에 오기 때문이다.
   - 예: `딸기우유`는 `우유`가 뒤에 있으니 우유. `우유식빵`은 빵. `참치김밥`은 도시락/즉석류. `사과주스`는 음료.
2. 끝 위치가 같으면 **긴 키워드**를 고른다.
   - 예: `양파`와 `파`가 둘 다 맞으면 `양파`. `새우깡`과 `새우`면 `새우깡`. `물티슈`와 `물`이면 `물티슈`.
3. 이름에 `냉동`이 있으면 무조건 냉동식품(freezer, 90일)으로 한다.
4. 비식품 키워드에 걸리면 `non_food`로 하고, `is_food=false`, 소비기한 null로 둔다.
5. 아무것도 안 맞으면 `other` (냉장, 7일).
6. 위험한 1글자 키워드는 넣지 않는다.
   - 넣지 말 것: `국`, `탕`(설탕·국수 오매칭), `차`, `물`, `란`, `회`, `파`, `봉`, `번`.

### 8.2 카테고리 사전

일수는 구매일 기준의 보수적인 추정치다. 코드에서 dict 하나로 관리하고, 나중에 쉽게 고칠 수 있게 한다.

| key | 라벨 | 보관 | 일수 | 키워드 예 |
|---|---|---|---|---|
| milk | 우유 | fridge | 10 | 우유, 락토프리, 저지방우유 |
| yogurt | 요거트 | fridge | 14 | 요거트, 요구르트, 요플레, 야쿠르트, 불가리스, 액티비아 |
| cheese | 치즈 | fridge | 30 | 치즈, 모짜렐라 |
| butter | 버터·크림 | fridge | 60 | 버터, 마가린, 크림치즈, 생크림 |
| egg | 달걀 | fridge | 30 | 계란, 달걀, 유정란, 메추리알 |
| tofu | 두부 | fridge | 7 | 두부, 순두부, 연두부, 유부 |
| beef | 소고기 | fridge | 3 | 소고기, 쇠고기, 한우, 등심, 안심, 채끝, 차돌, 우삼겹, 불고기, 양지, 사태 |
| pork | 돼지고기 | fridge | 3 | 돼지, 돈육, 삼겹, 목살, 앞다리, 항정, 대패 |
| chicken | 닭고기 | fridge | 2 | 닭, 닭가슴살, 닭다리, 생닭 |
| minced | 다진고기 | fridge | 2 | 다짐육, 다진 |
| processed_meat | 햄·소시지 | fridge | 21 | 햄, 소시지, 베이컨, 스팸, 비엔나, 프랑크 |
| seafood | 생선·해산물 | fridge | 2 | 생선, 고등어, 연어, 갈치, 오징어, 새우, 조개, 바지락, 홍합, 굴, 전복, 낙지, 문어, 꽃게, 동태 |
| fish_cake | 어묵·맛살 | fridge | 14 | 어묵, 오뎅, 맛살, 크래미 |
| kimchi | 김치·절임 | fridge | 30 | 김치, 깍두기, 열무, 겉절이, 단무지, 젓갈, 장아찌 |
| side_dish | 반찬·샐러드 | fridge | 5 | 반찬, 나물, 무침, 볶음, 조림, 샐러드 |
| ready_meal | 도시락·즉석조리 | fridge | 2 | 도시락, 김밥, 삼각김밥, 샌드위치, 햄버거, 주먹밥, 밀키트, 찌개, 떡볶이, 곰탕, 육개장 |
| leafy_veg | 잎채소·나물류 | fridge | 4 | 상추, 깻잎, 시금치, 배추, 청경채, 부추, 콩나물, 숙주, 대파, 쪽파, 미나리 |
| vegetable | 채소 | fridge | 7 | 오이, 애호박, 가지, 파프리카, 피망, 고추, 브로콜리, 토마토, 버섯, 팽이, 양배추 |
| root_veg | 뿌리채소 | room | 21 | 감자, 고구마, 양파, 당근, 무, 마늘, 생강, 단호박 |
| fruit | 과일 | fridge | 7 | 사과, 배, 귤, 오렌지, 레몬, 포도, 샤인머스캣, 키위, 참외, 수박, 복숭아, 망고, 파인애플, 단감 |
| soft_fruit | 무른 과일 | fridge | 4 | 딸기, 바나나, 블루베리, 체리 |
| bread | 빵 | room | 4 | 빵, 식빵, 베이글, 크루아상, 케이크, 카스테라, 도넛 |
| rice_cake | 떡 | room | 3 | 떡, 가래떡, 인절미, 송편 |
| frozen | 냉동식품 | freezer | 90 | 냉동, 만두, 아이스크림, 너겟, 동그랑땡, 피자 |
| makgeolli | 막걸리 | fridge | 10 | 막걸리 |
| drink | 음료 | room | 180 | 주스, 음료, 콜라, 사이다, 커피, 라떼, 녹차, 보리차, 생수, 삼다수, 두유, 식혜, 맥주, 소주, 에이드 |
| snack | 과자 | room | 180 | 과자, 스낵, 초콜릿, 쿠키, 젤리, 사탕, 새우깡, 감자칩, 비스킷, 시리얼 |
| instant | 라면·즉석식품 | room | 180 | 라면, 컵라면, 짜파게티, 즉석밥, 햇반, 컵밥, 카레, 짜장 |
| canned | 통조림 | room | 365 | 통조림, 참치, 꽁치, 골뱅이, 스위트콘 |
| sauce | 양념·소스 | room | 365 | 간장, 된장, 고추장, 쌈장, 케첩, 마요네즈, 소스, 식초, 참기름, 식용유, 설탕, 소금, 굴소스, 올리고당, 꿀, 잼 |
| grain | 곡물·면 | room | 365 | 쌀, 현미, 잡곡, 밀가루, 부침가루, 국수, 소면, 파스타, 당면 |
| dried | 건어물·견과 | room | 180 | 견과, 아몬드, 땅콩, 호두, 김, 멸치, 미역, 다시마, 황태 |
| non_food | 비식품 | - | - | 휴지, 물티슈, 키친타올, 세제, 섬유유연제, 샴푸, 치약, 칫솔, 비누, 종량제, 봉투, 지퍼백, 랩, 호일, 고무장갑, 건전지, 마스크 |
| other | 기타 | fridge | 7 | (기본값) |

### 8.3 추정 함수

```python
def estimate(name: str, purchased_on: date, product: ProductInfo | None = None)
    -> Estimate(category, category_label, storage, is_food, expiry_date, expiry_source)
```

1. `product.shelf_life_days`가 있으면 `purchased_on + days`로 계산하고 `source="barcode"`로 둔다. 카테고리는 이름으로 분류하되, 이름이 `other`면 `product.food_type`(식품유형)으로 한 번 더 분류해 본다.
2. 없으면 카테고리 기본 일수로 계산하고 `source="category"`로 둔다.

### 8.4 `POG_DAYCNT` 문구 → 일수 변환

- 문구를 `,` `/` `;` 로 나눈 뒤, 숫자가 있는 첫 조각만 쓴다.
- 그 조각 안의 `N년`(365), `N개월|N달`(30), `N주`(7), `N일`(1)을 모두 더한다.
- 숫자가 없으면("별도표기" 등) None으로 한다.

테스트할 문구와 기대값:

| 입력 | 기대 결과 |
|---|---|
| `제조일로부터 12개월` | 360 |
| `제조일로부터 1년 6개월` | 545 |
| `10일` | 10 |
| `냉장 14일, 냉동 6개월` | 14 |
| `별도표기` | None |

---

## 9. OCR 엔진 (`services/ocr`)

```python
class OcrEngine(Protocol):
    def recognize(self, image_path: str) -> list[OcrBox]: ...
```

### 9.1 `PaddleOcrEngine`
- **처음 호출될 때만 모델을 로딩한다.** 모듈 전역 싱글톤으로 두고 `threading.Lock`으로 감싼다.
- 생성 인자:
  ```python
  PaddleOCR(lang=settings.ocr_lang, use_doc_orientation_classify=False,
            use_doc_unwarping=False, use_textline_orientation=True)
  ```
- PaddleOCR 3.x는 `ocr.predict(path)`의 결과 항목에서 `rec_texts`, `rec_scores`, `rec_boxes`(또는 `rec_polys`)를 꺼낸다.
  - 결과 객체가 dict처럼 동작하지 않으면 `.json["res"]`를 시도한다.
  - `rec_boxes`가 없으면 `rec_polys`의 꼭짓점으로 박스를 계산한다.
  - 이렇게 **방어적으로** 짜 줘.

### 9.2 엔진 선택과 실행
- `get_ocr_engine()`을 FastAPI dependency로 만든다. 테스트에서 `app.dependency_overrides`로 `FakeOcrEngine`을 끼울 수 있게 하기 위해서다.
- `OCR_ENGINE=none`이면 빈 결과를 반환하는 엔진을 쓴다. 이 경우 초안이 비고 warnings가 붙는다.
- OCR은 CPU 연산이므로 라우터는 `def`(동기)로 두거나 `run_in_threadpool`로 실행한다.
- 나중에 로컬 VLM(Ollama의 Qwen3-VL 등)으로 바꿀 수 있게 인터페이스만 지켜 두면 된다. 이번에는 구현하지 않는다.

---

## 10. 설정 (`.env.example`)

```
DATABASE_URL=postgresql+psycopg://fridge:fridge@db:5432/fridge
JWT_SECRET=change-me
ACCESS_TOKEN_MINUTES=30
REFRESH_TOKEN_DAYS=14
EMAIL_BACKEND=console        # smtp로 바꾸면 실제 발송
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=               # Gmail 앱 비밀번호
MAIL_FROM=
UPLOAD_DIR=uploads
MAX_UPLOAD_MB=10
OCR_ENGINE=paddle            # paddle | none
OCR_LANG=korean
FOODSAFETY_API_KEY=          # 비우면 바코드 조회 비활성 (카테고리 추정만)
CORS_ORIGINS=*
AUTO_CREATE_TABLES=true
TIMEZONE=Asia/Seoul
```

`JWT_SECRET`이 기본값인 채로 `EMAIL_BACKEND=smtp`이면(운영으로 간주) 시작할 때 경고 로그를 남긴다.

---

## 11. Docker

### 11.1 Dockerfile
- `python:3.11-slim`을 기반으로 한다.
- 시스템 패키지: `libgl1`, `libglib2.0-0`, `libgomp1` (paddle/opencv용).
- `requirements.txt`와 `requirements-ocr.txt`를 모두 설치한다.
- PaddleOCR 모델 캐시 경로는 볼륨으로 마운트해서 재시작할 때 다시 받지 않게 한다. 실제 캐시 경로는 확인해서 README에 적어 줘.
- `uvicorn app.main:app --host 0.0.0.0 --port 8000`으로 실행한다. 워커는 1개다 (OCR 모델 메모리 때문).

### 11.2 docker-compose
- `db`(postgres:16, healthcheck 포함)와 `api`(depends_on: db healthy)를 둔다.
- 볼륨: `pgdata`, `uploads`, `paddle_models`.

---

## 12. 테스트 (pytest), 반드시 전부 통과시킬 것

### 12.1 테스트 환경
- `DATABASE_URL=sqlite://`(인메모리, StaticPool), `EMAIL_BACKEND=console`, `OCR_ENGINE=none`.
- 테스트마다 테이블을 초기화한다.
- `FastAPI.TestClient`를 쓴다.
- paddle은 설치하지 않은 환경에서도 테스트가 돌아야 한다. paddle import는 엔진 안에서 지연 import한다.

### 12.2 필수 테스트

**`test_auth.py`**
- 코드 발송 → OUTBOX에서 코드 추출 → verify → signup → login
- 아이디 중복 409, 이메일 중복 409
- 틀린 코드 5회 후 차단
- 60초 재발송 429
- 인증 토큰 재사용 거부
- find-id
- reset-password 후 옛 비밀번호 로그인 실패, 옛 리프레시 토큰 401
- 없는 이메일로 find_id 코드 요청해도 같은 메시지

**`test_fridges.py`**
- 가입 시 기본 냉장고 생성
- 초대코드로 다른 유저 참여
- 비멤버 접근 404
- owner 나가기 400, 멤버 나가기 성공, owner가 멤버 내보내기

**`test_parser.py`**: 7.6의 4가지 레이아웃

**`test_expiry.py`**
- 8.1의 오매칭 사례들: 딸기우유, 우유식빵, 양파, 새우깡, 물티슈, 설탕, 냉동삼겹살, 단무지
- 8.4의 `POG_DAYCNT` 문구들

**`test_receipts.py`**
- FakeOcrEngine으로 업로드 → 초안 확인 → 일부 수정 후 확정 → 식품 생성 확인 (비식품은 receipt_items에만)
- 영수증 바코드 중복 409 (바코드 디코딩 함수는 monkeypatch)
- 확정된 영수증 재확정 409

**`test_items_stats.py`**
- 수동 추가 시 자동 소비기한
- expiring에 지난 것 포함, days_left 음수
- 반만 먹기 → 나머지 버리기 → status=discarded
- 월별 통계의 지출, 먹은 금액, 버린 금액, waste_rate 값 검증
- purchases summary 집계

**`test_foodsafety.py`**
- httpx를 목킹해서 C005 응답을 파싱하고 캐시 적중을 확인
- 키 없을 때 `source="disabled"`

---

## 13. README.md (한국어)

아래 내용을 넣어 줘.
1. 기능 소개
2. 로컬 실행: venv, docker-compose
3. `.env` 설명
4. 식품안전나라 API 키 발급 방법: 식품안전나라 회원가입 → OpenAPI 이용신청
5. Gmail SMTP 앱 비밀번호 설정
6. **API 사용 흐름 예시** (curl):
   - 회원가입 3단계
   - 영수증 업로드 → 확정
   - 먹음/버림
   - 임박 목록
   - 월별 통계
7. 영수증 인식 한계와 팁: 평평하게, 밝게, 영수증만 꽉 차게 찍기
8. "확인 필요" 목록: C005 필드명, PaddleOCR 모델 캐시 경로 등 검증 못 한 것들
9. 향후 확장: 푸시 알림(FCM), VLM 엔진 교체, S3 이미지 저장, 레시피 추천

---

## 14. 작업 순서와 완료 기준

1. 뼈대: config, DB, 모델, Alembic 초기 마이그레이션
2. 인증 + 테스트
3. 냉장고 공유 + 테스트
4. `expiry.py`, `receipt_parser.py` (순수 로직) + 테스트. **여기서 사례 테스트를 충분히 만든다.**
5. 식품 CRUD, 먹음/버림 + 테스트
6. OCR 엔진, 영수증 업로드·확정 + 테스트
7. 식품안전나라 연동 + 테스트
8. 구매 기록·통계 + 테스트
9. Docker, README

**완료 기준**
- `pytest` 전부 통과.
- `docker compose up`으로 뜨고, `/docs`(Swagger)에서 모든 엔드포인트가 한국어 summary와 함께 보인다.
- 실제 영수증 사진 1장으로 업로드 → 초안이 나오는 것까지 확인한다. 사진이 없으면 내가 줄 테니 요청해.

## 15. 하지 말 것

- 유료 OCR 또는 외부 AI API(CLOVA, Google Vision, OpenAI 등)를 추가하지 않는다.
- 업로드 직후 확인 없이 바로 냉장고에 넣지 않는다.
- 인증코드나 비밀번호를 평문으로 저장하거나 로그로 남기지 않는다. console 메일 백엔드는 예외(개발용).
- 계정 존재 여부를 응답 차이로 노출하지 않는다. 회원가입 이메일 중복만 예외.
- 범위 밖 기능(푸시 알림, 소셜 로그인, 관리자 페이지)은 만들지 말고 README의 "향후 확장"에만 적는다.
