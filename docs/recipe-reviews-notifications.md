# 레시피 별점·후기, 알림 아이콘

로그인(Bearer 토큰)이 필요한 경로들이다. 시각은 모두 UTC(`...Z`)로 온다.

## 레시피 별점과 후기

레시피 **자체**에 남기는 것이라 냉장고와 상관없이 로그인한 모두가 쓰고 본다. 레시피 번호(`recipe_id`)는 추천 카드의 `id`다(`mafra:…`, `10000recipe:…`처럼 `:`가 들어 있어도 그대로 쓴다).

| 경로 | 설명 |
|---|---|
| `PUT /recipes/{recipe_id}/reviews/mine` | 별점과 후기 남기기. 본문 `{"rating": 1~5, "body": "후기"}`. `body`는 500자까지, 비우거나 공백이면 별점만 남는다. **이미 썼으면 고친다**(처음이면 201, 고치면 200). 없는 레시피는 404 |
| `GET /recipes/{recipe_id}/reviews?limit=&before_id=` | `summary`(평균, 개수, 별점별 분포), 내 후기 `my_review`, 후기 목록(최신순, 기본 20, 최대 50). 응답의 `next_before_id`를 다음 요청의 `before_id`로 넣는다. 없으면 마지막이다 |
| `DELETE /recipes/{recipe_id}/reviews/{review_id}` | 쓴 사람이나 관리자만. 남의 후기면 403 |
| `POST /recipes/{recipe_id}/reviews/{review_id}/report` | 후기 신고. 본문은 글 신고와 같다(`{"reason": ..., "detail": ...}`) |
| `GET /recipes/reviews/mine?limit=&before_id=` | 내가 쓴 후기 모아 보기(최신순). 후기마다 `recipe_id`, `recipe_name`이 들어 있어 눌러서 그 레시피로 갈 수 있다 |

- 한 사람이 한 레시피에 후기 하나다. 지우면 새로 쓸 수 있다. 지운 후기는 DB에 `deleted_at`, `deleted_by`(누가)로 남기고 보여 주지 않으며 평균과 개수에서도 빠진다.
- 후기는 한 시간에 20개까지(`REVIEWS_PER_HOUR`, TODO: balance. 지운 것도 센다). 고치는 것은 세지 않는다.
- `recipe_name`은 쓸 때의 이름을 복사해 둔 것이다. 레시피 자료를 다시 가져와 그 레시피가 없어져도 후기와 목록은 그대로 보인다(그때는 새로 쓰거나 고치지는 못한다).
- 후기 목록은 레시피 자료에 그 번호가 없어도 200으로 빈 목록(또는 남아 있는 후기)을 준다.
- 후기의 `mine`(내가 쓴 것), `can_delete`(지울 수 있는 것)로 앱이 버튼을 그린다.
- 후기는 받은 그대로 돌려준다(HTML로 해석하지 않는다).

### 기존 레시피 응답에 붙은 것

- `POST /fridges/{id}/recipes/recommendations`: 카드마다 `rating_average`(평균, 소수 첫째 자리. 후기가 없으면 `null`)와 `rating_count`.
- `GET /fridges/{id}/recipes/{recipe_id}`: `rating`(`average`, `count`, `distribution`)과 `my_review_id`(내가 쓴 후기 번호, 없으면 `null`). 후기 목록과 쓰기는 위 경로로 따로 부른다.

### 신고와 관리자

후기도 글·댓글처럼 신고할 수 있다. `GET /admin/reports`에 `target_type: "review"`로 오고, `target`에 `recipe_id`, `recipe_name`, `text`(별점과 본문 앞부분)가 들어 있다(`post_id`는 `null`). `POST /admin/reports/{id}/resolve`로 지우거나 문제없음으로 닫는 규칙은 글·댓글과 같다. 자세한 규칙은 [community.md](community.md)의 "신고와 관리자 처리".

## 알림 아이콘

푸시가 아니라 **앱이 열려 있을 때 부르는 조회**다. 아이콘에 붙일 숫자와, 눌렀을 때 보여 줄 내용을 준다.

| 경로 | 설명 |
|---|---|
| `GET /notifications/summary` | `{"pending_change_requests": n, "unread_change_results": n, "unread_comments": n, "total": n}`. 앱을 열 때와 앱으로 돌아올 때 이것만 부르면 된다 |
| `GET /notifications/comments?limit=&before_id=` | **내 글에 달린 댓글 내용**(최신순, 기본 20, 최대 50). 댓글마다 `comment_id`, `post_id`, `post_title`, `commenter`(닉네임, 사진), `body`, `created_at`, `unread`. `unread_count`(새 댓글 전체 수)와 `next_before_id`도 온다 |
| `GET /notifications/change-results?limit=&before_id=` | **내가 보낸 변경 요청의 결과**(승인·거절된 것만, 최신순, 기본 20, 최대 50). 요청 내용(`item_name`, `kind`, `fridge_name`, `note` 등 `/change-requests/sent`와 같은 칸)에 `status`(`approved`/`declined`), `handled_at`, `owner`(처리한 냉장고 주인), `unread`가 붙는다. `unread_count`와 `next_before_id`도 온다 |
| `POST /notifications/change-results/read` | 확인함으로 표시하고 새 `summary`를 돌려준다. 본문 `{"ids": [요청 번호, ...]}`(200개까지)는 선택이며 화면에 보여 준 요청 번호를 넣는다. 본문을 비우면 지금까지 처리된 결과 모두 확인함이다 |
| `POST /notifications/comments/read` | 읽음으로 표시하고 새 `summary`를 돌려준다. 본문 `{"up_to_id": 댓글 번호}`는 선택이며, 화면에 보여 준 가장 새 `comment_id`를 넣으면 그 뒤에 달린 댓글은 새 것으로 남는다. 본문을 비우면 지금까지 달린 것 모두 읽음이다 |

- **변경 요청**: `pending_change_requests`는 내가 주인인 냉장고에 들어와 아직 승인·거절하지 않은 요청 수다. 아이콘을 누르면 기존 `GET /change-requests/received`(기본이 처리 전인 것)로 내용을 보여 주고, 승인·거절하면 숫자가 줄어든다. 읽음 표시는 따로 없다.
- **댓글**: 다른 사람이 내 글에 단 댓글만 센다. 내가 단 댓글, 지운 댓글, 지운 글의 댓글은 뺀다. 읽음은 사람마다 따로 기억하고(`users.comments_seen_id`), 번호는 올라가기만 해서 오래된 화면에서 보낸 요청이 읽은 자리를 되돌리지 않는다. 존재하지 않는 큰 번호를 보내도 앞으로 달릴 댓글까지 읽음이 되지는 않는다.
- **내가 보낸 변경 요청의 결과**: `unread_change_results`는 내가 보낸 요청 중 주인이 승인하거나 거절했고 내가 아직 확인하지 않은 것의 수다. 내가 취소한 요청과 친구가 끊겨 자동으로 취소된 요청은 결과가 아니라서 세지 않는다. 확인 표시는 요청마다 따로 남고(`change_requests.result_seen_at`), 다른 사람의 요청 번호를 보내도 영향이 없다.

## 적용

기존 DB 백업 후 새 이미지에서 `alembic upgrade head`를 실행하고 서버를 다시 켠다(배포 순서의 `migrate` 단계).

- `0006`: 새 표 `recipe_reviews`, `users`에 `comments_seen_id` 칸 추가. **이미 달린 댓글은 읽은 것으로 시작한다**(마이그레이션이 현재 가장 큰 댓글 번호를 넣어 준다). 그래서 배포 직후 옛 댓글이 한꺼번에 새 알림으로 뜨지 않는다.
- `AUTO_CREATE_TABLES`는 이미 있는 `users` 표에 칸을 더하지 못한다. 로컬 개발 DB는 `alembic stamp 0001` 다음 `alembic upgrade head`(자세한 것은 [community.md](community.md)의 "배포할 때").
- `0007`: `change_requests`에 `result_seen_at` 칸을 더한다. **이미 처리된 요청은 확인한 것으로 시작한다**(승인·거절된 요청의 `handled_at`을 넣어 준다).
- 되돌리기: `alembic downgrade 0006`은 `result_seen_at`만, `0005`까지 내리면 후기 표와 댓글 읽음 칸도 지운다.

검증: `tests/test_recipe_reviews.py`, `tests/test_notifications.py`.
