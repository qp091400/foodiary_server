# 냉장고 참여자 초대 · 커뮤니티

친구 기능은 제거했다. 사용자는 냉장고별 초대 링크로 참여한다. 모든 API는 로그인이 필요하다.

## 초대 흐름

1. 냉장고 주인이 앱의 냉장고 설정 → 참여자 초대·권한 관리에 들어간다.
2. `보기만(viewer)` 또는 `함께 관리(editor)`를 선택하고 링크를 만든다. 앱에서는 기본 7일이다.
3. 상대가 `/share/{token}`을 열고 로그인한 뒤 초대를 확인하고 참여한다.
4. 함께 관리는 냉장고 화면에서 재고를 수정한다. 보기만은 읽기 전용 식품 목록을 연다.

| API | 역할 |
|---|---|
| POST /fridges/{id}/share-links | 주인이 초대 생성 |
| GET /share-links/{token} | 냉장고·주인·권한 미리보기 |
| POST /share-links/{token}/join | 해당 냉장고에만 참여 권한 부여 |
| GET /shared-fridges | 직접 초대받은 냉장고 목록. 전체 공개만으로 보이는 냉장고는 제외 |
| DELETE /shared-fridges/{id} | 참여자가 해당 냉장고에서 나가기 |
| GET /fridges/{id}/access | 주인이 참여자 조회 |
| PATCH /fridges/{id}/access/{user_id} | 주인이 보기만/함께 관리 권한 변경 |
| DELETE /fridges/{id}/access/{user_id} | 주인이 해당 냉장고 참여 해제 |
| DELETE /fridges/{id}/share-links/{link_id} | 초대 종료. 기존 참여 권한은 유지 |

보기 전용 참여자는 재고 수정이나 새 변경 요청을 보낼 수 없다. 주인만 참여 권한을 관리한다.
링크 만료·취소·참여 인원 제한, 재참여의 멱등성은 유지한다. 같은 권한으로 재참여해도 참여 인원을 다시 세지 않는다.
하나의 냉장고 참여를 해제해도 다른 냉장고 권한에는 영향을 주지 않는다.

## 이전 데이터 호환

- `/friends` API와 친구 코드를 외부 응답에서 제거했다. 초대로 친구 관계를 생성하지 않는다.
- 공개 범위는 `private`(참여자만)와 `public`(전체 공개) 두 가지다.
- Alembic 0008은 기존 `friends` 공개 범위를 `private`로 바꾼다. 이전 친구 관계만으로 접근을 허용하지 않는다.
- 직접 초대받은 viewer와 editor 권한은 유지한다. 과거 친구·변경 요청 테이블은 이력 보존을 위해 삭제하지 않는다.
- 기존 변경 요청의 조회·처리 API는 이력 호환을 위해 남기지만, 새 요청과 앱의 요청 화면·알림은 제공하지 않는다.
- 이전 초대코드 API는 구버전 호환용으로 남기고 새 앱은 링크 흐름만 제공한다.

## 커뮤니티

| 경로 | 설명 |
|---|---|
| `POST /posts` | 글쓰기. 제목 1~100자, 본문 1~2000자(앞뒤 공백은 지운다). 한 시간에 10개까지(`POSTS_PER_HOUR`, TODO: balance. 지운 글도 센다) |
| `GET /posts?limit=&before_id=` | 최신순 목록(기본 20, 최대 50). 응답의 `next_before_id`를 다음 요청의 `before_id`로 넣는다. 없으면 마지막이다 |
| `GET /posts/{id}` | 글 하나 |
| `DELETE /posts/{id}` | 쓴 사람이나 관리자만. 남의 글이면 403. 붙인 사진 파일도 함께 지워진다 |

- 글에는 `mine`(내가 쓴 글), `can_delete`(지울 수 있는 글), `like_count`, `liked_by_me`, `comment_count`, `photos`(사진 목록)가 같이 와서 앱이 따로 묻지 않아도 화면을 그릴 수 있다.
- 지운 글은 DB에 `deleted_at`, `deleted_by`(누가)로 남기고 목록과 상세에서 뺀다.
- 글은 받은 그대로 돌려준다(HTML로 해석하지 않는다). 화면에 그릴 때 문자 그대로 보여 주는 것은 앱의 몫이다.

## 사진 (2차)

글을 쓴 다음 사진을 붙이는 두 단계다(글 쓰기는 JSON 그대로).

| 경로 | 설명 |
|---|---|
| `POST /posts/{id}/photos` | 사진 붙이기. 폼 데이터의 `files`에 여러 장. **내가 쓴 글에만**, 글 하나에 4장까지(`MAX_PHOTOS_PER_POST`, TODO: balance). 응답은 사진이 붙은 글 |
| `GET /posts/{id}/photos/{photo_id}` | 사진 받기(JPEG). 로그인 토큰이 필요하다. 글의 `photos[].url`이 이 경로다 |
| `DELETE /posts/{id}/photos/{photo_id}` | 사진 빼기. 글쓴이나 관리자 |

- jpg, png, webp를 받아서 **JPEG로 다시 저장한다(긴 변 2000px까지 줄임). 이때 EXIF(촬영 위치, 기기 등)가 빠진다.**
- 한 번에 올린 사진 중 하나라도 사진이 아니면 아무것도 붙이지 않는다.
- Cloud Run은 요청 하나가 32MiB까지라서, 앱이 사진을 긴 변 2000px 안팎으로 줄여서 올려야 한다(영수증 올릴 때와 같다).
- 파일은 `uploads/posts/{글 번호}/`에 둔다. 지금 `uploads`는 운영에서 비공개 버킷에 붙어 있고, 사진은 로그인한 사용자에게만 이 API로 내려 준다.

## 좋아요 · 댓글 (2차)

| 경로 | 설명 |
|---|---|
| `PUT /posts/{id}/like` · `DELETE /posts/{id}/like` | 좋아요 / 취소. 두 번 눌러도 하나이고, 응답은 `{like_count, liked_by_me}` |
| `POST /posts/{id}/comments` | 댓글 쓰기. 1~500자. 한 시간에 30개까지(`COMMENTS_PER_HOUR`, TODO: balance. 지운 것도 센다) |
| `GET /posts/{id}/comments?after_id=&limit=` | 댓글 목록(오래된 것부터, 기본 30, 최대 100). `next_after_id`를 다음 요청의 `after_id`로 넣는다 |
| `DELETE /posts/{id}/comments/{comment_id}` | 쓴 사람이나 관리자. 글쓴이도 남의 댓글은 못 지운다 |

## 신고와 관리자 처리 (2차)

레시피 후기도 같은 방식으로 신고하고 처리한다(`target_type: "review"`, [recipe-reviews-notifications.md](recipe-reviews-notifications.md)).

| 경로 | 설명 |
|---|---|
| `POST /posts/{id}/report` | 글 신고. 본문 `{"reason": ..., "detail": ...}` (`detail`은 300자까지, 생략 가능) |
| `POST /posts/{id}/comments/{comment_id}/report` | 댓글 신고 |
| `GET /admin/reports?status=open\|resolved\|dismissed\|all` | **관리자만.** 신고 목록(기본은 처리 안 한 것, 오래된 것부터). 신고한 사람, 신고된 글·댓글의 내용 앞부분, 같은 대상에 쌓인 신고 수가 같이 온다 |
| `POST /admin/reports/{id}/resolve` | **관리자만.** `{"action": "delete"}`는 대상을 지우고(글이면 사진도) `resolved`, `{"action": "dismiss"}`는 문제없음으로 `dismissed`. 같은 대상에 들어온 다른 신고도 같은 결과로 닫힌다 |

- 사유(`reason`): `spam`(광고, 도배), `abuse`(욕설, 괴롭힘), `inappropriate`(부적절, 불쾌), `privacy`(개인정보), `other`.
- 내가 쓴 글과 댓글은 신고할 수 없고(400), 같은 사람이 같은 대상을 또 신고할 수 없다(409). 문제없음으로 닫힌 뒤에도 같다.
- **신고가 쌓여도 글이 저절로 숨겨지지는 않는다.** 관리자가 `GET /admin/reports`로 보고 처리해야 한다. 운영할 때는 관리자가 신고를 정기적으로 확인하는 사람이어야 하고, 필요하면 "신고 3건이면 자동으로 숨김" 같은 규칙을 더할 수 있다.

## 관리자 만들기

```sh
python -m scripts.set_admin 이메일            # 관리자로 만들기
python -m scripts.set_admin 이메일 --remove   # 빼기
```

운영 DB(Neon)에서는 SQL 편집기에서 `UPDATE users SET is_admin = true WHERE email = '이메일';`을 실행해도 된다.

## 배포할 때

- DB가 바뀐다: `users`에 `friend_code`(이미 가입한 사람에게는 마이그레이션이 하나씩 만들어 준다), `is_admin`, `fridges`에 `visibility`(기본 `private`), 새 표 `friendships`, `posts`(마이그레이션 `0002`), `post_likes`, `comments`, `post_photos`, `reports`(`0003`), `fridge_viewers`, `fridge_share_links`, `change_requests`(`0004`). 새 칸과 표만 더해진다. 단 `0004`는 답을 못 받은 예전 친구 요청(`friendships.status = 'pending'`)을 지운다(요청 방식을 없앴으므로). 마이그레이션은 배포 순서의 `migrate` 단계에서 `deploy`보다 먼저 돈다.
- `friend_code`는 기본값 없이 필수라서, 마이그레이션 뒤에 **옛 버전의 서버로 되돌리면 새 가입이 실패한다**. 되돌릴 일이 생기면 `alembic downgrade 0001`을 먼저 한다. (`downgrade 0002`는 2차 표만 지운다. 올린 사진 파일은 `uploads/posts/`에 남으니 필요하면 따로 지운다.)
- 예전 친구 요청 방식의 경로(`/friends/requests...`, `/users/search`, `/users/lookup`, `POST /users/me/friend-code`)는 없어졌다. 앱이 쓰고 있었다면 링크 방식으로 바꿔야 한다.
- 로컬 개발 DB는 `AUTO_CREATE_TABLES`로 만들어져서 새 칸이 자동으로 안 생긴다. 한 번만: `alembic stamp 0001` 다음 `alembic upgrade head`.

## 아직 없는 것

- 닉네임 검색으로 친구 찾기, 요청을 보내고 상대가 승인하는 방식(지금은 코드를 넣으면 바로 친구다). 친구를 모든 냉장고에 한꺼번에 권한 주기(지금은 냉장고마다 링크를 만든다).
- **사용자 차단**(그 사람의 글과 댓글을 안 보기). 사용자가 글을 올리는 앱은 스토어 심사에서 신고, 차단, 이용약관을 요구받을 수 있어서(확인 필요) 앱을 공개하기 전에 차단과 약관은 준비해야 한다.
- 신고가 쌓이면 자동으로 숨기기, 푸시 알림(앱을 열었을 때 보는 알림 아이콘은 있다), 글 수정, 사진 썸네일.
