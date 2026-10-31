# 푸시 알림 운영

`0012` 마이그레이션은 `push_devices`와 `push_deliveries`만 추가하며 기존 데이터를 변경하지 않는다. 먼저 `alembic upgrade head`, 다음 서버, 마지막 웹을 배포한다.

웹 API는 인증된 사용자의 `/notifications/push/config`, `PUT/DELETE /notifications/push/devices/{UUID}`만 제공한다. 공개 발송 API는 없다. 기기 ID는 설치마다 생성하고, 구독 주소/Expo 토큰의 SHA-256으로 중복 등록을 방지한다. 웹 구독은 알려진 HTTPS 푸시 제공자의 주소와 실제 P-256 공개 키/16바이트 auth 키만 허용한다. 발송 요청은 리다이렉트를 따라가지 않는다.

환경 변수:

- API: `PUSH_WEB_PUBLIC_KEY`(VAPID P-256 공개 점의 base64url), `PUSH_EXPO_ENABLED`(설치 앱 인증 준비 전에는 false).
- 작업: 기존 `DATABASE_URL` 비밀 참조, `TIMEZONE=Asia/Seoul`, `PUSH_VAPID_PRIVATE_KEY`(32바이트 개인 키의 base64url; Secret Manager), `PUSH_VAPID_SUBJECT`(연락 가능한 운영 HTTPS 주소 또는 mailto). 개인 키는 Git·이미지·웹에 넣지 않는다.
- 선택: Expo의 강화된 인증 사용 시 작업에 `PUSH_EXPO_ACCESS_TOKEN` 비밀 참조를 추가한다. native 활성화 시 EAS 프로젝트의 Android FCM/iOS APNs 인증도 먼저 설정한다.

비공개 Cloud Run Job `foodiary-push`는 서버와 같은 이미지로 `python -m scripts.send_push_notifications`를 실행한다. OCR 엔진을 시작하지 않으며 1 CPU/512Mi, taskCount=1, parallelism=1, maxRetries=0으로 구성한다. 필요한 DB 연결 설정은 마이그레이션 작업과 동일하게 유지한다. Cloud Scheduler `foodiary-push-minute`에서 `* * * * *`, Asia/Seoul, HTTP POST `https://run.googleapis.com/v2/projects/PROJECT/locations/REGION/jobs/foodiary-push:run`을 호출한다. 전용 서비스 계정의 OAuth cloud-platform 토큰을 사용하고 해당 작업에만 `roles/run.invoker`를 부여한다. 공개 실행 권한이나 개인 계정의 장기 키는 사용하지 않는다.

매 배포 시 마이그레이션 작업과 서비스뿐 아니라 `foodiary-push` 작업의 이미지도 같은 불변 digest로 갱신한다. `scripts/cloudrun.py` 배포 구성 JSON에 `push_web_public_key`를 저장하고 설치 앱 인증을 마쳤을 때만 `push_expo_enabled: true`를 설정한다. 이 구성으로 배포하면 API의 공개 키를 유지하며 기존 푸시 작업의 이미지도 함께 갱신한다. 푸시 작업·스케줄·개인 키는 최초 한 번 별도로 준비한다. 개인 키를 회전하면 기존 브라우저 구독도 새 공개 키로 재등록해야 하므로 정상 운영 중 무심코 재생성하지 않는다.

기능 규칙:

- 기기·이벤트별 DB 유일성으로 성공한 발송을 반복하지 않는다. 등록 전 댓글은 보내지 않는다. 발송이 실패해도 댓글 작성은 영향을 받지 않는다.
- 1분마다 최신 댓글을 수집한다. 소비기한은 한국 시간 09시 이후, 오늘~3일 이내 남은 식품을 냉장고별 하루 한 번 보낸다. 만료된 지난날 식품은 매일 반복해 보내지 않는다.
- 발송 전에 읽은 댓글, 삭제된 글/댓글, 해제된 설정, 냉장고 권한, 계정 비밀번호 토큰 버전을 확인한다.
- 네트워크·제공자 일시 오류는 지수 지연 후 최대 5회 시도한다. 웹 404/410 및 Expo DeviceNotRegistered는 기기를 비활성화한다. Expo의 accepted 티켓은 15분 뒤 영수증으로 확인하며 티켓을 받았다는 이유로 재발송하지 않는다.
- PostgreSQL 연결 하나에 advisory lock을 유지해 겹치는 작업을 막는다. 요청 전 DB에 재시도 시각을 기록한다. 제공자가 수락한 직후 프로세스가 죽는 경우 분산 트랜잭션이 아니므로 재전송 가능성이 있으며 웹의 안정된 알림 tag로 중복 표시를 줄인다.
- 활동이 90일 넘은 기기는 보내지 않고 발송 이력은 30일 후 정리한다. 구독 주소·토큰·메시지 본문을 로그에 남기지 않는다.

검증은 `pytest` 및 `tests/test_push.py`를 사용한다. 실제 수신은 명시적으로 동의한 휴대폰에서 확인해야 한다. 운영 전체 사용자에게 테스트 알림을 보내지 않는다.
