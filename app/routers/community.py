"""커뮤니티: 글쓰기와 지우기, 사진, 좋아요, 댓글, 신고. 글은 로그인한 모두에게 보인다."""

import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models import Comment, Post, PostLike, PostPhoto, RecipeReview, User
from app.routers.receipts import load_image, read_upload
from app.schemas import (
    CommentCreate,
    CommentList,
    CommentOut,
    LikeOut,
    MessageResponse,
    PostCreate,
    PostBoard,
    PostUpdate,
    PostReviewOut,
    PostList,
    PostOut,
    PostPhotoOut,
    PublicUser,
    ReportCreate,
)
from app.services.community import delete_comment, delete_post, file_report, remove_photo_file
from app.services.post_search import find_posts
from app.timeutil import utcnow

router = APIRouter(prefix="/posts", tags=["커뮤니티"])

POSTS_PER_HOUR = 10  # TODO: balance. 지운 글도 센다(지우고 다시 써서 도배하는 것을 막는다)
COMMENTS_PER_HOUR = 30  # TODO: balance. 지운 댓글도 센다
MAX_PHOTOS_PER_POST = 4  # TODO: balance
POST_NOT_FOUND = "글을 찾을 수 없어요."
COMMENT_NOT_FOUND = "댓글을 찾을 수 없어요."


def _photo_url(post_id: int, photo_id: int) -> str:
    return f"/posts/{post_id}/photos/{photo_id}"


def _decorate(db: Session, rows: list[tuple[Post, User]], me: User) -> list[PostOut]:
    """글마다 좋아요 수, 내가 눌렀는지, 댓글 수, 사진을 한꺼번에 모아서 붙인다 (글마다 따로 묻지 않는다)."""
    ids = [post.id for post, _ in rows]
    likes: dict[int, int] = {}
    liked: set[int] = set()
    comments: dict[int, int] = {}
    photos: dict[int, list[PostPhotoOut]] = {}
    review_ids = [post.review_id for post, _ in rows if post.review_id is not None]
    reviews = {review.id: review for review in db.scalars(select(RecipeReview).where(RecipeReview.id.in_(review_ids))).all()} if review_ids else {}
    if ids:
        likes = dict(db.execute(select(PostLike.post_id, func.count()).where(PostLike.post_id.in_(ids)).group_by(PostLike.post_id)).all())
        liked = set(db.scalars(select(PostLike.post_id).where(PostLike.post_id.in_(ids), PostLike.user_id == me.id)).all())
        comments = dict(
            db.execute(
                select(Comment.post_id, func.count()).where(Comment.post_id.in_(ids), Comment.deleted_at.is_(None)).group_by(Comment.post_id)
            ).all()
        )
        for photo in db.scalars(select(PostPhoto).where(PostPhoto.post_id.in_(ids)).order_by(PostPhoto.id)).all():
            photos.setdefault(photo.post_id, []).append(PostPhotoOut(id=photo.id, url=_photo_url(photo.post_id, photo.id)))
    return [
        PostOut(
            id=post.id,
            author=PublicUser.of(author),
            title=post.title,
            body=post.body,
            board=post.board,
            review=PostReviewOut(id=review.id, recipe_id=review.recipe_id, recipe_name=review.recipe_name, rating=review.rating)
            if (review := reviews.get(post.review_id)) else None,
            updated_at=post.updated_at,
            can_edit=post.author_id == me.id and post.review_id is None,
            created_at=post.created_at,
            mine=post.author_id == me.id,
            can_delete=post.author_id == me.id or me.is_admin,
            like_count=likes.get(post.id, 0),
            liked_by_me=post.id in liked,
            comment_count=comments.get(post.id, 0),
            photos=photos.get(post.id, []),
        )
        for post, author in rows
    ]


def _live_post(db: Session, post_id: int) -> tuple[Post, User]:
    row = db.execute(
        select(Post, User).join(User, User.id == Post.author_id).where(Post.id == post_id, Post.deleted_at.is_(None))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=POST_NOT_FOUND)
    return row


def _live_comment(db: Session, post_id: int, comment_id: int) -> tuple[Comment, User]:
    _live_post(db, post_id)
    row = db.execute(
        select(Comment, User)
        .join(User, User.id == Comment.author_id)
        .where(Comment.id == comment_id, Comment.post_id == post_id, Comment.deleted_at.is_(None))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=COMMENT_NOT_FOUND)
    return row


def _too_many(what: str, limit: int) -> HTTPException:
    return HTTPException(status_code=429, detail=f"{what}은(는) 한 시간에 {limit}개까지 쓸 수 있어요. 잠시 후에 다시 써 주세요.", headers={"Retry-After": "600"})


# ── 글 ───────────────────────────────────────────────────────────────────


@router.post("", response_model=PostOut, status_code=201, summary="글쓰기 (사진은 글을 쓴 뒤 POST /posts/{id}/photos로 붙인다)")
def create_post(payload: PostCreate, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    recent = db.scalar(
        select(func.count()).select_from(Post).where(Post.author_id == me.id, Post.review_id.is_(None), Post.created_at >= utcnow() - timedelta(hours=1))
    )
    if recent >= POSTS_PER_HOUR:
        raise _too_many("글", POSTS_PER_HOUR)
    post = Post(author_id=me.id, title=payload.title, body=payload.body, board=payload.board)
    db.add(post)
    db.commit()
    return _decorate(db, [(post, me)], me)[0]


@router.get("", response_model=PostList, summary="글 목록·제목/본문 검색 (최신순). 다음 쪽도 같은 q·board를 보낸다")
def list_posts(
    q: str = Query("", max_length=100, description="제목·본문에 포함된 검색어. 빈 값·공백이면 전체 목록"),
    board: PostBoard | None = Query(None, description="general: 자유게시판, recipe: 레시피게시판. 생략하면 전체"),
    before_id: int | None = Query(None, ge=1, description="이 번호보다 오래된 글부터"),
    limit: int = Query(20, ge=1, le=50),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = find_posts(db, query=q, board=board, before_id=before_id, limit=limit)
    page = rows[:limit]
    return PostList(posts=_decorate(db, page, me), next_before_id=page[-1][0].id if len(rows) > limit else None)


@router.get("/{post_id}", response_model=PostOut, summary="글 하나 보기")
def get_post(post_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _decorate(db, [_live_post(db, post_id)], me)[0]


@router.patch("/{post_id}", response_model=PostOut, summary="내 글의 제목·본문·게시판 수정")
def update_post(post_id: int, payload: PostUpdate, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post, author = _live_post(db, post_id)
    if post.author_id != me.id:
        raise HTTPException(status_code=403, detail="내가 쓴 글만 수정할 수 있어요.")
    if post.review_id is not None:
        raise HTTPException(status_code=409, detail="자동 등록된 후기는 레시피의 내 후기에서 수정해 주세요.")
    changes = payload.model_dump(exclude_unset=True)
    if any(getattr(post, key) != value for key, value in changes.items()):
        for key, value in changes.items():
            setattr(post, key, value)
        post.updated_at = utcnow()
        db.commit()
    return _decorate(db, [(post, author)], me)[0]


@router.delete("/{post_id}", status_code=204, summary="글 지우기 (쓴 사람 또는 관리자). 붙인 사진도 함께 지워진다")
def remove_post(post_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post, _ = _live_post(db, post_id)
    if post.author_id != me.id and not me.is_admin:
        raise HTTPException(status_code=403, detail="내가 쓴 글만 지울 수 있어요.")
    delete_post(db, post, me.id)
    return Response(status_code=204)


# ── 사진 ─────────────────────────────────────────────────────────────────


@router.post("/{post_id}/photos", response_model=PostOut, status_code=201, summary=f"사진 붙이기 (내가 쓴 글에, 글 하나에 {MAX_PHOTOS_PER_POST}장까지)")
def add_photos(
    post_id: int,
    files: list[UploadFile] = File(description="사진들 (jpg/png/webp, 장당 최대 10MB). 앱에서 긴 변 2000px 안팎으로 줄여서 올리는 것이 좋다"),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    post, author = _live_post(db, post_id)
    if post.author_id != me.id:
        raise HTTPException(status_code=403, detail="내가 쓴 글에만 사진을 붙일 수 있어요.")
    attached = db.scalar(select(func.count()).select_from(PostPhoto).where(PostPhoto.post_id == post.id))
    if attached + len(files) > MAX_PHOTOS_PER_POST:
        raise HTTPException(status_code=400, detail=f"사진은 글 하나에 {MAX_PHOTOS_PER_POST}장까지 붙일 수 있어요.")
    # 먼저 모두 열어 보고(하나라도 사진이 아니면 아무것도 저장하지 않는다) 다시 저장한다. 다시 저장하면 EXIF(촬영 위치 등)가 빠진다.
    images = [load_image(read_upload(file)) for file in files]
    saved: list[Path] = []
    try:
        for image in images:
            relative = f"posts/{post.id}/{uuid.uuid4().hex}.jpg"
            target = Path(settings.upload_dir) / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, "JPEG", quality=85)
            saved.append(target)
            db.add(PostPhoto(post_id=post.id, path=relative))
        db.commit()
    except Exception:
        db.rollback()
        for target in saved:
            target.unlink(missing_ok=True)
        raise
    return _decorate(db, [(post, author)], me)[0]


def _photo_of(db: Session, post_id: int, photo_id: int) -> PostPhoto:
    photo = db.scalar(select(PostPhoto).where(PostPhoto.id == photo_id, PostPhoto.post_id == post_id))
    if photo is None:
        raise HTTPException(status_code=404, detail="사진을 찾을 수 없어요.")
    return photo


@router.get("/{post_id}/photos/{photo_id}", summary="사진 받기 (JPEG)", response_class=FileResponse)
def get_photo(post_id: int, photo_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _live_post(db, post_id)
    photo = _photo_of(db, post_id, photo_id)
    path = Path(settings.upload_dir) / photo.path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="사진을 찾을 수 없어요.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/{post_id}/photos/{photo_id}", status_code=204, summary="사진 빼기 (글쓴이 또는 관리자)")
def remove_photo(post_id: int, photo_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post, _ = _live_post(db, post_id)
    if post.author_id != me.id and not me.is_admin:
        raise HTTPException(status_code=403, detail="내가 쓴 글의 사진만 뺄 수 있어요.")
    photo = _photo_of(db, post_id, photo_id)
    remove_photo_file(photo)
    db.delete(photo)
    db.commit()
    return Response(status_code=204)


# ── 좋아요 ───────────────────────────────────────────────────────────────


def _like_state(db: Session, post_id: int, me: User) -> LikeOut:
    count = db.scalar(select(func.count()).select_from(PostLike).where(PostLike.post_id == post_id))
    mine = db.scalar(select(PostLike.post_id).where(PostLike.post_id == post_id, PostLike.user_id == me.id)) is not None
    return LikeOut(like_count=count, liked_by_me=mine)


@router.put("/{post_id}/like", response_model=LikeOut, summary="좋아요 (이미 눌렀으면 그대로)")
def like_post(post_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _live_post(db, post_id)
    if db.scalar(select(PostLike.post_id).where(PostLike.post_id == post_id, PostLike.user_id == me.id)) is None:
        db.add(PostLike(post_id=post_id, user_id=me.id))
        try:
            db.commit()
        except IntegrityError:  # 동시에 두 번 눌렀다
            db.rollback()
    return _like_state(db, post_id, me)


@router.delete("/{post_id}/like", response_model=LikeOut, summary="좋아요 취소 (안 눌렀으면 그대로)")
def unlike_post(post_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _live_post(db, post_id)
    like = db.get(PostLike, (post_id, me.id))
    if like is not None:
        db.delete(like)
        db.commit()
    return _like_state(db, post_id, me)


# ── 댓글 ─────────────────────────────────────────────────────────────────


def _comment_out(comment: Comment, author: User, me: User) -> CommentOut:
    return CommentOut(
        id=comment.id,
        post_id=comment.post_id,
        author=PublicUser.of(author),
        body=comment.body,
        created_at=comment.created_at,
        mine=comment.author_id == me.id,
        can_delete=comment.author_id == me.id or me.is_admin,
    )


@router.post("/{post_id}/comments", response_model=CommentOut, status_code=201, summary="댓글 쓰기")
def create_comment(post_id: int, payload: CommentCreate, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _live_post(db, post_id)
    recent = db.scalar(
        select(func.count()).select_from(Comment).where(Comment.author_id == me.id, Comment.created_at >= utcnow() - timedelta(hours=1))
    )
    if recent >= COMMENTS_PER_HOUR:
        raise _too_many("댓글", COMMENTS_PER_HOUR)
    comment = Comment(post_id=post_id, author_id=me.id, body=payload.body)
    db.add(comment)
    db.commit()
    return _comment_out(comment, me, me)


@router.get("/{post_id}/comments", response_model=CommentList, summary="댓글 목록 (오래된 것부터). 다음 쪽은 after_id에 next_after_id를 넣어 부른다")
def list_comments(
    post_id: int,
    after_id: int | None = Query(None, ge=1, description="이 번호보다 새 댓글부터"),
    limit: int = Query(30, ge=1, le=100),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _live_post(db, post_id)
    query = select(Comment, User).join(User, User.id == Comment.author_id).where(Comment.post_id == post_id, Comment.deleted_at.is_(None))
    if after_id is not None:
        query = query.where(Comment.id > after_id)
    rows = db.execute(query.order_by(Comment.id).limit(limit + 1)).all()
    page = rows[:limit]
    return CommentList(
        comments=[_comment_out(comment, author, me) for comment, author in page],
        next_after_id=page[-1][0].id if len(rows) > limit else None,
    )


@router.delete("/{post_id}/comments/{comment_id}", status_code=204, summary="댓글 지우기 (쓴 사람 또는 관리자)")
def remove_comment(post_id: int, comment_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    comment, _ = _live_comment(db, post_id, comment_id)
    if comment.author_id != me.id and not me.is_admin:
        raise HTTPException(status_code=403, detail="내가 쓴 댓글만 지울 수 있어요.")
    delete_comment(db, comment, me.id)
    return Response(status_code=204)


# ── 신고 ─────────────────────────────────────────────────────────────────


@router.post("/{post_id}/report", response_model=MessageResponse, status_code=201, summary="글 신고하기")
def report_post(post_id: int, payload: ReportCreate, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post, _ = _live_post(db, post_id)
    return file_report(db, me, "post", post.id, post.author_id, payload)


@router.post("/{post_id}/comments/{comment_id}/report", response_model=MessageResponse, status_code=201, summary="댓글 신고하기")
def report_comment(post_id: int, comment_id: int, payload: ReportCreate, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    comment, _ = _live_comment(db, post_id, comment_id)
    return file_report(db, me, "comment", comment.id, comment.author_id, payload)
