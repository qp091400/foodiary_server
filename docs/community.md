# 친구 · 냉장고 공유 · 변경 요청 · 커뮤니티

로그인한 사용자끼리 **링크로 친구를 맺고**, 주인이 준 권한만큼 서로의 냉장고를 보거나 고치고, 보기 권한만 있는 친구는 **변경 요청**을 보내고, 글을 써서 나누는 기능이다. 모든 경로는 로그인(Bearer 토큰)이 필요하다.

## 정한 것

- **친구는 친구 코드나 냉장고 공유 링크로 맺는다.**
  - **친구 코드**: 상대의 친구 코드(8자)를 넣으면 **받는 쪽의 승인 없이 바로 서로 친구가 된다.** 친구가 되면 서로 `friends`로 공개한 냉장고가 보이고, 보기 권한이 있는 냉장고에는 변경 요청을 보낼 수 있다. 친구 코드는 글·댓글 작성자 정보에도 나오므로 **글을 본 사람은 누구나 그 작성자와 친구가 될 수 있다**(싫으면 `DELETE /friends/{user_id}`로 끊는다).
  - **공유 링크**: 주인이 냉장고마다 링크를 만들어 보내면(시간표 공유처럼), 링크로 들어온 사람은 주인과 친구가 되고 그 냉장고의 권한을 받는다. 닉네임으로 요청을 보내거나 검색하는 방식은 없앴다.
- **친구 권한은 냉장고마다 둘이다.**
  - `viewer`(기본): 보기만 하고, 주인에게 **변경 요청**을 보낼 수 있다.
  - `editor`: 주인처럼 고칠 수 있다(품목 추가, 수정, 먹음, 버림). 냉장고 지우기, 공개 범위, 링크, 권한 관리는 주인만 한다.
- **친구 코드는 한 번 받으면 바뀌지 않는다.** 닉네임은 겹쳐도 되고(가입할 때 직접 입력), 같은 닉네임은 친구 코드로 구별한다. 내 코드는 `GET /users/me`의 `friend_code`로 받아 앱이 보여 주고 공유한다.
- 냉장고 공개 범위도 있다(`private` 나만 기본 / `friends` 친구들 모두 / `public` 모두). 링크로 받은 권한과 별개로 "누구에게나 보이게"를 하고 싶을 때 쓴다.
- 커뮤니티 글은 로그인한 모두에게 보인다. 글은 쓴 사람이 지울 수 있고, **관리자는 누구 글이든 지울 수 있다**. 관리자는 `users.is_admin`이고 API로는 바꿀 수 없다.

## 공유 링크와 친구

| 경로 | 설명 |
|---|---|
| `POST /fridges/{id}/share-links` | 링크 만들기(주인만). 본문 `{"permission": "viewer"\|"editor", "expires_in_days": 7, "max_uses": null}`. 응답의 `token`으로 앱이 링크를 만든다. 서버에 `SHARE_LINK_BASE`를 설정해 두면 `url`도 온다 |
| `GET /fridges/{id}/share-links` | 살아 있는 링크(주인만). 만료, 취소, 다 쓴 링크는 빠진다 |
| `DELETE /fridges/{id}/share-links/{link_id}` | 링크 없애기. 이미 들어온 사람의 권한은 그대로다 |
| `GET /share-links/{token}` | 링크를 눌렀을 때 "○○님의 냉장고 [이름]"과 받게 될 권한, 내가 이미 가진 권한을 보여 주는 미리 보기 |
| `POST /share-links/{token}/join` | 링크로 들어오기: 주인과 친구가 되고 권한을 받는다 |
| `POST /friends` | **친구 코드로 친구 추가.** 본문 `{"friend_code": "ABCD2345"}`(앞뒤 공백과 대소문자는 무시). 새로 친구가 되면 201, 이미 친구면 200이고 둘 다 상대의 `user`와 `since`가 온다. 없는 코드는 404, 내 코드는 400 |
| `GET /friends` | 내 친구 목록(닉네임, 친구 코드) |
| `DELETE /friends/{user_id}` | 친구 끊기. 서로의 냉장고에서 받았던 권한이 모두 사라지고, 기다리던 변경 요청은 취소된다 |

- 친구 코드를 넣어 볼 수 있는 횟수는 한 사람이 1분에 10번이다(`CODE_TRIES_PER_MINUTE`, TODO: balance. 틀린 코드도 센다. 코드를 아무거나 넣어 맞히는 것을 막는다). 넘으면 429와 `Retry-After`가 온다. 서버를 여러 개 띄우면 개수마다 따로 센다.
- 링크는 기본 7일(최대 90일) 동안 살아 있고, `max_uses`로 들어올 수 있는 사람 수를 정할 수 있다. 냉장고 하나에 살아 있는 링크는 20개까지(`MAX_ACTIVE_LINKS`, TODO: balance).
- 권한은 **올라가기만 한다**: 보기 권한인 사람이 고치기 링크로 들어오면 고치기가 되지만, 고칠 수 있는 사람이 보기 링크를 눌러도 내려가지 않는다. 이미 그만큼의 권한이 있는 사람이 링크를 다시 눌러도 횟수가 늘지 않고, 다 쓴 링크도 열린다.
- 내 링크로 내가 들어올 수는 없다(400). 링크가 없거나 만료되었으면 404.
- 링크 주소 모양(웹 주소나 앱 딥링크)은 앱과 정해야 한다. 서버는 `token`만 알면 된다.

## 주인이 친구별 권한 관리

| 경로 | 설명 |
|---|---|
| `GET /fridges/{id}/access` | 이 냉장고에 권한이 있는 사람과 `viewer`/`editor`(주인만) |
| `PATCH /fridges/{id}/access/{user_id}` | 권한 바꾸기 `{"permission": "viewer"\|"editor"}` |
| `DELETE /fridges/{id}/access/{user_id}` | 이 냉장고의 권한 거두기. 친구는 그대로다 |

(예전 초대 코드 방식 `POST /fridges/{id}/invites`, `POST /fridges/join`은 그대로 있다. 코드로 들어온 사람은 `editor`와 같고 친구가 되지는 않는다.)

## 변경 요청

보기 권한이 있는 **친구**가 주인에게 보낸다. 주인이 승인하면 그때 냉장고에 반영되고, 거절하면 아무 일도 없다.

| 경로 | 설명 |
|---|---|
| `POST /change-requests` | 요청 보내기. `kind`가 `add`면 `fridge_id`, `name`(선택: `quantity`, `category`, `storage`, `expiry_date`), `consume`(먹음)·`discard`(버림)이면 `fridge_id`, `item_id`(선택: `quantity`, 비우면 남은 양 전부). 모두 `note`(200자)를 붙일 수 있다 |
| `GET /change-requests/sent?status=` | 내가 보낸 요청(최신순) |
| `DELETE /change-requests/{id}` | 처리 전인 내 요청 취소 |
| `GET /change-requests/received?status=&fridge_id=` | **내 냉장고들**에 들어온 요청(기본은 처리 전, 오래된 것부터) |
| `POST /change-requests/{id}/approve` | 승인: 냉장고에 반영(주인만). 그 사이 품목이 없어졌거나 이미 다 먹었으면 400/404가 나고 요청은 처리 전으로 남는다 |
| `POST /change-requests/{id}/decline` | 거절(주인만) |

- 요청을 보낼 수 있는 사람: 그 냉장고를 볼 수 있고 **주인의 친구**인 `viewer`. 주인과 `editor`는 직접 고치면 되니 400, 친구가 아닌데 공개 냉장고를 보고 있는 사람은 403, 볼 수 없는 냉장고는 404다.
- 반영된 기록에는 **요청한 사람**이 남는다(누가 먹었는지, 누가 넣었는지). 승인과 거절은 주인만 한다(`editor`도 못 한다).
- 한 사람이 한 냉장고에 답을 못 받은 요청은 20개까지(`MAX_PENDING_PER_FRIDGE`, TODO: balance).
- 푸시 알림은 아직 없다. 앱이 `GET /change-requests/received`로 주인에게 보여 주고, 아이콘에 붙일 처리 전 요청 수는 `GET /notifications/summary`로 받는다([recipe-reviews-notifications.md](recipe-reviews-notifications.md)).

## 남의 냉장고 보기

| 경로 | 설명 |
|---|---|
| `PATCH /fridges/{id}` | 본문에 `{"visibility": "private" \| "friends" \| "public"}`. 냉장고 주인만. `name`과 같이 또는 따로 보낸다 |
| `GET /users/{user_id}/fridges` | 그 사람이 주인인 냉장고 중 **내가 볼 수 있는 것**, 남은 품목 수, 내 권한(`my_permission`), 변경 요청을 보낼 수 있는지(`can_request_changes`) |
| `GET /users/{user_id}/fridges/{fridge_id}/items` | 그 냉장고의 남은 품목(소비기한이 빠른 순, 기본 200개). 변경 요청에 쓸 `id`가 들어 있다 |

- 볼 수 있는 사람: 그 냉장고의 멤버(주인, `editor`) / 링크로 받은 `viewer` / `friends`이면 주인의 친구 / `public`이면 로그인한 모두.
- 보이는 정보는 품목 이름, 분류, 보관 방법, 수량, 구매일, 소비기한, 남은 일수뿐이다. **가격, 메모, 바코드, 영수증, 누가 넣었는지는 숨긴다.** 먹거나 버린 품목도 안 보인다.
- 볼 수 없으면 있는지 없는지도 알려 주지 않고 404를 준다. 이 경로로는 고치거나 지울 수 없다(고치려면 `editor`가 되거나 변경 요청을 보낸다).

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
