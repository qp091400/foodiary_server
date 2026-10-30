# 친구 · 남의 냉장고 보기 · 커뮤니티

로그인한 사용자끼리 친구를 맺고, 주인이 허락한 만큼 서로의 냉장고를 보고, 글을 써서 나누는 기능이다. 모든 경로는 로그인(Bearer 토큰)이 필요하다.
1차는 친구, 냉장고 공개 범위, 글쓰기와 지우기이고, 2차는 글에 사진 붙이기, 좋아요, 댓글, 신고와 관리자 처리다.

## 정한 것

- 친구 찾기: **친구 코드(링크에 담아 공유)**, **닉네임**, 닉네임 검색에서 고른 사람. 이메일로는 찾지 않는다(그 이메일이 가입자인지 알려 주게 되므로).
- 냉장고 공개 범위는 냉장고마다 주인이 고른다: `private`(나만, 기본값) / `friends`(친구들도) / `public`(모두).
- 커뮤니티 글은 로그인한 모두에게 보인다.
- 글은 쓴 사람이 지울 수 있고, **관리자는 누구 글이든 지울 수 있다**. 관리자는 `users.is_admin`이고 API로는 바꿀 수 없다.

## 친구

| 경로 | 설명 |
|---|---|
| `GET /users/me` | 내 정보에 `friend_code`(8자)와 `is_admin`이 들어 있다. 링크는 앱이 코드를 담아 만든다 |
| `POST /users/me/friend-code` | 친구 코드를 새로 만든다. 예전 코드와 링크는 못 쓴다 |
| `GET /users/lookup?code=` | 코드가 누구 것인지(닉네임)와 나와의 관계. 링크를 눌렀을 때 "○○님에게 친구 요청?"을 보여 주는 용도 |
| `GET /users/search?nickname=` | 닉네임 앞부분이 같은 사람 최대 20명(두 글자부터). 나와의 관계(`none` 아님, `friend` 친구, `requested` 내가 요청함, `incoming` 상대가 요청함)가 같이 온다 |
| `POST /friends/requests` | 요청 보내기. 본문은 `{"code": ...}`, `{"nickname": ...}`, `{"user_id": ...}` 중 **하나만**. 같은 닉네임이 여러 명이면 409(검색해서 고른다) |
| `GET /friends/requests?direction=incoming\|outgoing` | 받은 / 보낸 요청 |
| `POST /friends/requests/{id}/accept` · `/decline` | 받은 요청 수락 / 거절 |
| `DELETE /friends/requests/{id}` | 내가 보낸 요청 취소 |
| `GET /friends` | 내 친구 목록 |
| `DELETE /friends/{user_id}` | 친구 끊기 |

- 상대가 이미 나에게 요청해 둔 상태에서 내가 요청하면 바로 친구가 된다(`status: "friends"`).
- 답을 못 받은 요청은 50개까지(`MAX_PENDING_REQUESTS`, TODO: balance). 넘으면 429.
- 거절하거나 취소하면 기록이 지워져서 다시 보낼 수 있다.

## 남의 냉장고 보기 (읽기 전용)

| 경로 | 설명 |
|---|---|
| `PATCH /fridges/{id}` | 본문에 `{"visibility": "private" \| "friends" \| "public"}`. 냉장고 주인만. `name`과 같이 또는 따로 보낸다 |
| `GET /users/{user_id}/fridges` | 그 사람이 주인인 냉장고 중 **내가 볼 수 있는 것**과 남은 품목 수 |
| `GET /users/{user_id}/fridges/{fridge_id}/items` | 그 냉장고의 남은 품목(소비기한이 빠른 순, 기본 200개) |

- 볼 수 있는 사람: 그 냉장고의 멤버(공유 냉장고) / `friends`이면 주인의 친구 / `public`이면 로그인한 모두.
- 보이는 정보는 품목 이름, 분류, 보관 방법, 수량, 구매일, 소비기한, 남은 일수뿐이다. **가격, 메모, 바코드, 영수증, 누가 넣었는지는 숨긴다.** 먹거나 버린 품목도 안 보인다.
- 볼 수 없으면 있는지 없는지도 알려 주지 않고 404를 준다. 친구를 끊으면 바로 닫힌다.
- 이 경로로는 고치거나 지울 수 없다. 공유 냉장고(초대 코드로 같이 쓰는 것)와는 다른 기능이다.

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

- DB가 바뀐다: `users`에 `friend_code`(이미 가입한 사람에게는 마이그레이션이 하나씩 만들어 준다), `is_admin`, `fridges`에 `visibility`(기본 `private`), 새 표 `friendships`, `posts`(마이그레이션 `0002`), `post_likes`, `comments`, `post_photos`, `reports`(`0003`). 새로 더해지기만 하고 지워지는 것은 없다. 마이그레이션은 배포 순서의 `migrate` 단계에서 `deploy`보다 먼저 돈다.
- `friend_code`는 기본값 없이 필수라서, 마이그레이션 뒤에 **옛 버전의 서버로 되돌리면 새 가입이 실패한다**. 되돌릴 일이 생기면 `alembic downgrade 0001`을 먼저 한다. (`downgrade 0002`는 2차 표만 지운다. 올린 사진 파일은 `uploads/posts/`에 남으니 필요하면 따로 지운다.)
- 로컬 개발 DB는 `AUTO_CREATE_TABLES`로 만들어져서 새 칸이 자동으로 안 생긴다. 한 번만: `alembic stamp 0001` 다음 `alembic upgrade head`.

## 아직 없는 것

- **사용자 차단**(그 사람의 글과 댓글을 안 보기). 사용자가 글을 올리는 앱은 스토어 심사에서 신고, 차단, 이용약관을 요구받을 수 있어서(확인 필요) 앱을 공개하기 전에 차단과 약관은 준비해야 한다.
- 신고가 쌓이면 자동으로 숨기기, 알림, 글 수정, 사진 썸네일.
