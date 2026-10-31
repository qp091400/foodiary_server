# 프로필 사진

로그인한 계정이 사진 한 장을 저장/교체/삭제한다.

- POST /users/me/avatar: multipart의 file에 jpg/png/webp. 기존 업로드 크기 제한 적용.
- DELETE /users/me/avatar: 기본 아이콘으로 복원. 여러 번 호출해도 204.
- GET /users/{id}/avatar/{filename}: 로그인 사용자에게만 JPEG 제공.
- UserOut과 PublicUser에 avatar_url을 포함하므로 내 정보, 냉장고 멤버, 글/댓글 작성자에 사용할 수 있다.
- GET /users/{id}/profile: 닉네임과 사진 주소만 담은 공개 프로필을 제공. 이메일/로그인 아이디는 제외.
- 프로필의 냉장고는 기존 /users/{id}/fridges와 /users/{id}/fridges/{fridge_id}/items로 읽는다.
  전체 공개는 로그인한 모든 회원, 비공개는 주인과 공유 링크로 들어온 멤버만 볼 수 있다.
  볼 수 없는 냉장고 이름/개수는 프로필 목록에 노출하지 않는다. 품목 직접 조회도 서버에서 다시 검사한다.

사진은 방향 보정 후 512x512로 중앙 크롭하고 JPEG로 다시 저장한다.
EXIF를 보관하지 않는다. 버전별 파일명이므로 교체 후 기존 이미지 캐시와 구분된다.
파일은 기존 UPLOAD_DIR의 avatars 폴더/영구 업로드 볼륨에 두며 Git에 넣지 않는다.
다른 계정으로 사진을 변경/삭제하는 API는 제공하지 않는다.

## 적용

기존 DB 백업 후 새 이미지에서 alembic upgrade head를 실행하고 서버를 다시 켠다.
0005 마이그레이션은 users에 nullable avatar_path만 추가하며 기존 계정을 보존한다.
AUTO_CREATE_TABLES는 이미 존재하는 표의 컬럼을 갱신하지 못하므로 마이그레이션이 필요하다.

검증: tests/test_user_avatar.py (업로드, 변경, 삭제, 인증, 잘못된 파일, 커뮤니티 반영).
