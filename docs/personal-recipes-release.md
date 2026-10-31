# 2026-11-01 개인화 추천 · 즉시 댓글 푸시 반영

이 문서는 최초 배포 이력이다. 이후 요청으로 식사 인원 기능은 제거했으며 현재 동작과 0017 배포 순서는 [personal-recipes.md](personal-recipes.md)를 따른다.

- 서버: `foodiary-00030-mlb`, `asia-northeast1`.
- 이미지: `asia-northeast1-docker.pkg.dev/virtual-primer-512512-g0/foodiary/foodiary@sha256:8de58bdc53a3b53356172e36e2244f7bced715dd8f8533ca4860eca3d8bbf9c4`.
- `0016` 마이그레이션 작업 성공. 기존 계정의 인원은 NULL이며 내 정보에서 설정 가능하다.
- 비공개 `foodiary-push`도 동일 이미지로 갱신, 실행 성공. `foodiary-push-minute`은 ENABLED 유지.
- API에 `PUSH_IMMEDIATE_ENABLED=true`, 기존 작업의 VAPID 개인 키 Secret Manager 참조와 subject 추가. 나머지 서비스 설정 보존을 비교 검증했다. 키를 회전하지 않았다.
- Firebase `foodiary-35981` / `foodiary-app-ef2b5` 웹 배포 완료.

검증:

- Linux 새 서버 이미지: 951개 통과. 실제 전체 레시피 색인에서 달걀 선택 추천 6.025초, 재료 미선택 ‘김치’ 검색 10.329초(Cloud Build 검증 환경; 실제 휴대폰 응답 시간과 다름).
- 로컬 서버 검사: Windows 호환 전체 923개 통과·8개 생략. 이후 배포 도구의 즉시 푸시 설정 유지 기능 추가 후 관련 109개 통과.
- 앱: 타입 검사·린트 통과, 전체 166개 통과, 운영 웹 내보내기 성공.
- 운영 `/ready` 200 및 OCR ready, 로그인 보호 401, 내부 문서 비공개 404 확인. 빈 가입 요청의 422 응답에 `household_size` 필수 검증 확인(계정·메일 생성 없음).
- 운영 HTML 4개·푸시 워커·첫 실행 JS 묶음 6개의 SHA-256이 로컬 배포본과 일치. HTML/워커의 재검증 캐시 헤더 확인.

실제 아이폰에서 푸시 도착 시간을 측정하지는 않았다. 즉시 발송과 주기 작업의 중복 방지·실제 전송 함수 호출은 자동 검사로 확인했으며, 정상 사용자 전체에 시험 알림을 보내지 않았다. 인분 정보가 없는 레시피의 수량은 추정하지 않는다. 추천의 취향 반영은 [personal-recipes.md](personal-recipes.md)의 점수 규칙을 따른다.
