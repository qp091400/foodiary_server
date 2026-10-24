# Oracle Always Free 배포

이 구성은 **Ubuntu 24.04, Ampere A1 ARM64, 단일 API 워커**를 대상으로 한다.
PaddlePaddle 3.2.2에는 Python 3.11용 Linux ARM64 wheel이 있다.
설치 가능 여부와 실제 ARM 추론 성공은 다른 검사다. 아래 모델 검사를 서버에서도 반드시 실행한다.
Docker 의존성은 `constraints-production.txt`로 고정한다. 업데이트할 때는 잠금을 갱신하고 실제 OCR과 API를 다시 검사한다.

## 1. 계정과 무료 서버

1. [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/)에서 계정을 만든다.
   본인 확인, 카드 확인, 약관 동의는 계정 소유자가 직접 진행한다. 비밀키·카드·비밀번호를 채팅이나 Git에 올리지 않는다.
2. 홈 리전에서 **Always Free Eligible**인 Ubuntu 24.04 ARM 이미지를 선택한다.
3. Shape는 `VM.Standard.A1.Flex`, **2 OCPU / 12GB RAM**, 부트 디스크 **50GB**로 설정한다.
4. 콘솔이 무료 대상이라고 표시하는지 확인한다. 유료 Shape, 유료 로드밸런서, 유료 계정 업그레이드는 이 절차에 포함하지 않는다.
5. SSH 키는 개인 PC에 보관한다. 공개키만 VM에 등록한다.
6. 공인 IP를 할당하고 보유 도메인 또는 무료 DDNS 호스트의 A 레코드를 그 IP로 지정한다.

2026-10-21 공식 문서 기준 A1 무료 한도는 합계 **2 OCPU·12GB**, 부트/블록 볼륨은 합계 **200GB**다.
다른 서버와 디스크도 이 합계에 포함된다. 가입 직후 체험 크레딧으로 유료 자원을 만들지 않도록 구분한다.
자원 부족으로 생성이 안 될 수 있고, 장기간 저사용 인스턴스는 회수될 수 있으므로 외부 백업이 필요하다.

출처: [Always Free 공식 조건](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

## 2. 네트워크

OCI Security List 또는 NSG의 수신 규칙:

| 포트 | 허용 출발지 | 용도 |
|---|---|---|
| TCP 22 | 관리자 공인 IP/32 | SSH |
| TCP 80, 443 | 인터넷 | HTTPS 인증서 발급·API |

**8000과 5432는 공개하지 않는다.** 이 Compose는 두 포트를 호스트에 게시하지 않는다.
호스트 방화벽에서도 80/443을 허용해야 한다. Ubuntu 이미지의 기존 방화벽 규칙은 먼저 확인하고,
전체 방화벽을 끄거나 기존 규칙을 일괄 삭제하지 않는다. Docker 게시 포트는 UFW와 별도로 적용될 수 있다.
외부로는 HTTPS 모델 다운로드, DNS, SMTP STARTTLS 587이 필요하다. C005를 사용하면 HTTP 80도 필요하다.

## 3. 파일 전송과 설치

개발 PC에서:

```powershell
python -X utf8 scripts/package_release.py
scp -i <개인키경로> artifacts/foodiary-oracle.tar.gz ubuntu@<서버IP>:~/
ssh -i <개인키경로> ubuntu@<서버IP>
```

서버에서 새 디렉터리에 압축을 푼다:

```sh
mkdir -p ~/foodiary_server
tar -xzf ~/foodiary-oracle.tar.gz -C ~/foodiary_server
cd ~/foodiary_server
sudo sh deploy/bootstrap_ubuntu.sh
python3 scripts/configure_production.py --domain api.example.com --email owner@example.com
```

`api.example.com`, `owner@example.com`은 실제 값으로 바꾼다. 스크립트는 무작위 JWT/DB 비밀값을 생성하고
`.env.production`을 권한 600으로 저장한다. 기존 파일은 덮어쓰지 않는다.
파일을 서버 편집기로 열어 `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`을 채운다.
기본 Gmail 설정은 앱 비밀번호를 사용한다. 실제 웹 프런트 주소가 있으면 `CORS_ORIGINS`에도 반영한다.
운영은 console 메일, 기본 비밀키, 개발 도구, 자동 테이블 생성을 허용하지 않는다.

## 4. 실제 모델 검사

```sh
sudo docker compose --env-file .env.production -f compose.production.yml build api
sudo docker compose --env-file .env.production -f compose.production.yml run --rm --no-deps \
  --entrypoint python api scripts/check_ocr.py
```

첫 실행은 세 모델을 내려받는다. 성공하면 `warmup: passed`, 아키텍처, 최대 메모리, 모델 SHA256이 나온다.
`deploy/model-manifest.json`에 기록된 검증 모델과 파일 해시가 다르면 검사를 중단한다.
운영 시작 검사도 같은 해시를 검증한다. 모델을 변경할 때는 인식 품질을 검증한 뒤 기준 manifest도 함께 갱신한다.
`paddle_models` 볼륨은 모델과 다운로드 캐시를 보관한다. 재배포 때 이 볼륨을 지우지 않는다.
원격 AI API에 영수증을 보내지 않고 컨테이너의 CPU에서 추론한다.

실제 영수증 비교:

```sh
sudo docker compose --env-file .env.production -f compose.production.yml run --rm --no-deps \
  -v /home/ubuntu/receipt.jpg:/tmp/receipt.jpg:ro \
  --entrypoint python api scripts/check_ocr.py /tmp/receipt.jpg
```

보고서에는 영수증 원문이 들어가므로 공개 로그나 Git에 올리지 않는다.
로컬과 서버에서 같은 사진·모델 SHA256·`OCR_DET_MAX_SIDE`로 품목/총액을 비교한다.
1280은 속도 우선 초기값이다. 품목이 누락되면 0과 비교하여 실제 사진 기준으로 결정한다.
이 명령의 메모리/시간 값으로 서버 사양을 판단한다. QEMU 에뮬레이션 시간은 실제 A1 성능값이 아니다.

## 5. 시작과 외부 검증

```sh
sudo docker compose --env-file .env.production -f compose.production.yml config --quiet
sudo docker compose --env-file .env.production -f compose.production.yml up -d
sudo docker compose --env-file .env.production -f compose.production.yml ps
curl --fail https://api.example.com/ready
```

개발 PC의 프로젝트 가상환경에서 `python scripts/check_deployment.py https://api.example.com`을 실행하면
외부 HTTPS 연결, OCR 준비 상태, 인증 요구, 문서/개발 페이지 차단을 함께 확인한다.

API는 마이그레이션 후 실제 OCR 시험 추론을 완료해야 정상 시작한다.
`/health`는 프로세스 상태, `/ready`는 DB 연결과 OCR 준비 상태다.
모델 오류는 운영에서 503이며 영수증/이미지를 남기지 않는다. 이미 분석 중이면 503과 Retry-After를 준다.
API 문서(`/docs`, `/redoc`, `/openapi.json`)와 개발 페이지는 운영에서 닫힌다.
로그인 등 인증 요청은 IP별 분당 30회, OCR 업로드는 IP별 분당 10회로 제한한다.
공유망에서는 같은 IP 한도를 공유한다. 제한은 단일 프로세스 메모리에 있고 재시작 시 초기화된다.

Caddy가 자동 HTTPS를 발급·갱신한다. DNS와 80/443 접근이 맞아야 발급할 수 있다.
TLS 비밀키는 `caddy_data` 볼륨에 저장된다. API는 Caddy가 정리한 프록시 헤더만 받는 구조이므로
나중에 API 포트를 직접 공개하거나 다른 프록시를 추가하면 신뢰 프록시 설정도 함께 수정해야 한다.

계정 가입 → 로그인 → 실제 사진 업로드 → 초안 확인 → 확정까지 휴대폰에서도 검사한다.
이메일 인증이 실제로 도착하는지 확인한다. SMTP 자격 증명 설정만으로 발송 성공이 보장되지는 않는다.

## 6. 백업과 업데이트

```sh
sudo sh scripts/backup.sh
```

`backups/<UTC시각>/`에 PostgreSQL custom dump와 이미지 압축본을 생성한다.
서버 밖에도 복사한다. 같은 VM의 디스크는 인스턴스 회수·삭제에 대한 충분한 백업이 아니다.
DB와 이미지의 동시 스냅샷은 아니므로, 백업 중에는 영수증 등록·삭제 등 데이터 변경이 없는 시간대를 사용한다.
복구 전에는 빈 임시 PostgreSQL에 `pg_restore`하여 덤프를 확인한다.
이미 운영 중인 DB에 복구할 때는 현재 DB도 먼저 백업하고 덮어쓸 데이터 범위를 확인한다.

업데이트 전에 백업한 후 소스를 교체하고 다음을 실행한다:

```sh
sudo docker compose --env-file .env.production -f compose.production.yml up -d --build
```

`down -v`는 DB·이미지·모델·인증서 볼륨을 삭제하므로 사용하지 않는다.
서버 보안 업데이트도 정기적으로 적용한다. 이 구성은 한 서버를 사용하며 무중단 배포와 고가용성을 제공하지 않는다.
