# 냉장고 공유 · 커뮤니티

냉장고를 **공유 링크(또는 QR)**로 다른 사람과 같이 쓰고, 글을 써서 나누는 기능이다. 친구 기능은 없다. 초대받은 사람은 모두 함께 고친다(보기 전용은 없다). 모든 경로는 로그인(Bearer 토큰)이 필요하다.

## 정한 것

- **냉장고는 링크로만 공유한다.** 냉장고마다 링크가 하나 있다(에브리타임 시간표 공유처럼). 공유 버튼을 처음 누를 때 만들어지고, 그다음부터는 같은 링크가 나온다.
- **링크로 들어온 사람은 그 냉장고의 멤버**가 되어 주인처럼 고칠 수 있고(품목 추가, 수정, 먹음, 버림, 영수증), 그 냉장고가 **내 냉장고 목록**(`GET /fridges`)에 들어온다. 보기만 하는 권한과 변경 요청은 없다.
- 냉장고 지우기, 이름·공개 범위 바꾸기, 링크 바꾸기, 멤버 내보내기는 **주인만** 한다. 멤버는 공유 버튼으로 같은 링크를 받아 다른 사람에게 다시 보낼 수 있다.
- 링크는 **기한이 없다.** 주인이 **링크를 바꾸면** 이전 링크와 QR로는 더 들어올 수 없다(이미 들어온 멤버는 그대로). 내보낸 사람이 같은 링크로 다시 들어오지 못하게 하려면 내보낸 뒤 링크를 바꾼다.
- 냉장고 공개 범위는 `private`(나만, 기본) / `public`(로그인한 모두가 읽기만) 둘이다. `friends`는 없어졌다.
- 커뮤니티 글은 로그인한 모두에게 보인다. 글은 쓴 사람이 지울 수 있고, **관리자는 누구 글이든 지울 수 있다**. 관리자는 `users.is_admin`이고 API로는 바꿀 수 없다.

## 공유 링크와 QR

| 경로 | 설명 |
|---|---|
| `POST /fridges/{id}/share-link` | 공유 링크 받기(멤버 누구나). 처음이면 만들고, 그다음부터는 같은 링크. 응답 `{"token", "url"}`. `url`은 서버에 `SHARE_LINK_BASE`가 설정되어 있을 때만 오고, 없으면 앱이 `token`으로 `/share/{token}` 주소를 만든다 |
| `GET /fridges/{id}/share-link/qr?format=png\|svg` | 공유 링크 주소를 담은 QR 이미지(멤버 누구나, 기본 PNG). 휴대폰 카메라로 찍으면 링크가 열린다. 링크가 아직 없으면 만든다. 서버에 `SHARE_LINK_BASE`가 없으면 503 |
| `POST /fridges/{id}/share-link/reset` | 링크 바꾸기(주인만). 새 `token`(과 `url`)을 돌려준다 |
| `GET /share-links/{token}` | 링크를 열었을 때의 미리 보기: `fridge_id`, `fridge_name`, `owner`(닉네임, 사진), `member_count`, `already_member`(이미 멤버면 들어오기 대신 바로 그 냉장고를 연다) |
| `POST /share-links/{token}/join` | 링크로 들어오기. 응답 `{"fridge": 내 냉장고 목록의 한 줄과 같은 모양, "joined": 새로 들어왔으면 true}`. 이미 멤버(주인 포함)면 아무것도 바꾸지 않고 `joined: false` |
| `GET /fridges/{id}` | 냉장고와 멤버 목록 |
| `DELETE /fridges/{id}/members/{user_id}` | 멤버 내보내기(주인) / 나가기(본인) |

- 없는 링크, 바뀐 링크, 지운 냉장고의 링크는 404다. 멤버가 아닌 사람이 링크나 QR을 달라고 하면 404다(냉장고가 있는지도 알려 주지 않는다).
- 토큰은 22자 무작위 문자열이라 아무거나 넣어 맞힐 수 없다. 대신 **링크를 아는 사람은 누구나 들어와 고칠 수 있으니**, 원하지 않는 사람에게 퍼졌으면 링크를 바꾸고 그 사람을 내보낸다.
- QR은 `Cache-Control: no-store`로 보낸다(링크를 바꾸면 QR도 바뀌어야 해서). 로그인 토큰을 붙여 받아야 하므로 앱이 받아서 화면에 그리거나 사진으로 저장한다. PNG는 한 칸이 8픽셀이다(`QR_SCALE`).
- `SHARE_LINK_BASE`는 웹 앱의 `/share/` 주소다(예: `https://foodiary.kr/share/`). Cloud Run에서는 `python3 scripts/cloudrun.py set-share-link-base 주소`로 넣는다([deploy/CLOUD_RUN.md](../deploy/CLOUD_RUN.md)).

## 남의 냉장고 보기

| 경로 | 설명 |
|---|---|
| `PATCH /fridges/{id}` | 본문에 `{"visibility": "private" \| "public"}`. 냉장고 주인만. `name`과 같이 또는 따로 보낸다 |
| `GET /users/{user_id}/profile` | 공개 프로필: `id`, `nickname`, `avatar_url` |
| `GET /users/{user_id}/fridges` | 그 사람이 주인인 냉장고 중 **내가 볼 수 있는 것**, 남은 품목 수, 내 권한(`my_permission`: `owner` / `editor` 링크로 들어온 멤버 / `viewer` 공개된 것을 구경) |
| `GET /users/{user_id}/fridges/{fridge_id}/items` | 그 냉장고의 남은 품목(소비기한이 빠른 순, 기본 200개) |

- 볼 수 있는 사람: 그 냉장고의 멤버(주인, 링크로 들어온 사람) / `public`이면 로그인한 모두.
- 보이는 정보는 품목 이름, 분류, 보관 방법, 수량, 구매일, 소비기한, 남은 일수뿐이다. **가격, 메모, 바코드, 영수증, 누가 넣었는지는 숨긴다.** 먹거나 버린 품목도 안 보인다.
- 볼 수 없으면 있는지 없는지도 알려 주지 않고 404를 준다. 이 경로로는 고치거나 지울 수 없다(고치려면 공유 링크로 멤버가 된다).

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

- **`0009`가 친구 기능을 지운다**(그 앞의 `0008`은 `friends` 공개 범위를 `private`으로만 바꾼다). 표 `friendships`, `fridge_viewers`(보기 권한), `change_requests`(변경 요청), `fridge_share_links`(예전 링크), `fridge_invites`(초대코드)와 `users.friend_code`를 지우고, `fridges`에 `share_token`을 더한다. `friends`로 공개했던 냉장고는 `private`이 된다. **링크나 초대코드로 들어와 고칠 수 있던 멤버(`fridge_members`)는 그대로 남고, 보기만 하던 사람(`fridge_viewers`)은 고칠 수 있는 멤버로 바뀐다**(냉장고에 들어온 날짜는 보기 권한을 받은 날짜를 쓰고, 이미 멤버이거나 주인이면 다시 넣지 않는다). 그래서 아무도 접근을 잃지 않는다. 예전 링크와 초대코드로는 더 들어올 수 없다(새 링크를 보내야 한다). 지운 데이터는 `downgrade`로 돌아오지 않는다(빈 표만 다시 생기고, 보기 전용이던 사람은 멤버로 남는다).
- 친구·변경 요청·초대코드 경로(`/friends`, `/change-requests`, `/fridges/{id}/invites`, `/fridges/join`, `/fridges/{id}/share-links`, `/fridges/{id}/access`, `/notifications/change-results`)와 응답의 `friend_code`, `is_friend`, `can_request_changes`, 알림 숫자 `pending_change_requests`, `unread_change_results`가 없어졌다. **앱이 이것들을 쓰고 있으면 앱을 먼저 바꾼 뒤 서버를 배포한다.**
- DB가 바뀐다: `users`에 `friend_code`(이미 가입한 사람에게는 마이그레이션이 하나씩 만들어 준다), `is_admin`, `fridges`에 `visibility`(기본 `private`), 새 표 `friendships`, `posts`(마이그레이션 `0002`), `post_likes`, `comments`, `post_photos`, `reports`(`0003`), `fridge_viewers`, `fridge_share_links`, `change_requests`(`0004`). 새 칸과 표만 더해진다. 단 `0004`는 답을 못 받은 예전 친구 요청(`friendships.status = 'pending'`)을 지운다(요청 방식을 없앴으므로). 마이그레이션은 배포 순서의 `migrate` 단계에서 `deploy`보다 먼저 돈다.
- `0009` 뒤에 **옛 버전의 서버로 되돌리면** 지워진 표와 `friend_code`를 찾다가 가입과 공유가 실패한다. 되돌릴 일이 생기면 `alembic downgrade 0008`을 먼저 한다(빈 표와 새 친구 코드가 만들어진다). (`downgrade 0002`는 2차 표만 지운다. 올린 사진 파일은 `uploads/posts/`에 남으니 필요하면 따로 지운다.)
- 로컬 개발 DB는 `AUTO_CREATE_TABLES`로 만들어져서 새 칸이 자동으로 안 생긴다. 한 번만: `alembic stamp 0001` 다음 `alembic upgrade head`.

## 아직 없는 것

- 링크마다 기한이나 사용 횟수 정하기(지금은 냉장고마다 고정 링크 하나와 "링크 바꾸기"뿐), 보기만 하는 공유, 멤버마다 권한을 다르게 주기, 냉장고 하나의 멤버 수 제한.
- **사용자 차단**(그 사람의 글과 댓글을 안 보기). 사용자가 글을 올리는 앱은 스토어 심사에서 신고, 차단, 이용약관을 요구받을 수 있어서(확인 필요) 앱을 공개하기 전에 차단과 약관은 준비해야 한다.
- 신고가 쌓이면 자동으로 숨기기, 푸시 알림(앱을 열었을 때 보는 알림 아이콘은 있다), 글 수정, 사진 썸네일.
