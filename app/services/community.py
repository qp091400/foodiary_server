"""커뮤니티 글·댓글·레시피 후기 지우기와 신고하기. 글쓴이나 관리자가 지울 때와, 관리자가 신고를 처리할 때 같은 규칙을 쓴다."""

from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Comment, Post, PostPhoto, RecipeReview, Report, User
from app.schemas import MessageResponse, ReportCreate
from app.timeutil import utcnow


def remove_photo_file(photo: PostPhoto) -> None:
    (Path(settings.upload_dir) / photo.path).unlink(missing_ok=True)


def _mark_post_deleted(db: Session, post: Post, by_user_id: int) -> None:
    post.deleted_at, post.deleted_by = utcnow(), by_user_id
    for photo in db.scalars(select(PostPhoto).where(PostPhoto.post_id == post.id)).all():
        remove_photo_file(photo)
        db.delete(photo)


def delete_post(db: Session, post: Post, by_user_id: int) -> None:
    """후기에서 자동 등록된 글은 원본 후기와 함께 지운다."""
    _mark_post_deleted(db, post, by_user_id)
    if post.review_id is not None:
        review = db.get(RecipeReview, post.review_id)
        if review is not None and review.deleted_at is None:
            review.deleted_at, review.deleted_by = post.deleted_at, by_user_id
    db.commit()


def delete_comment(db: Session, comment: Comment, by_user_id: int) -> None:
    comment.deleted_at, comment.deleted_by = utcnow(), by_user_id
    db.commit()


def delete_review(db: Session, review: RecipeReview, by_user_id: int) -> None:
    review.deleted_at, review.deleted_by = utcnow(), by_user_id
    post = db.scalar(select(Post).where(Post.review_id == review.id, Post.deleted_at.is_(None)))
    if post is not None:
        _mark_post_deleted(db, post, by_user_id)
    db.commit()


def file_report(db: Session, me: User, target_type: str, target_id: int, target_author_id: int, payload: ReportCreate) -> MessageResponse:
    """신고를 접수한다. 내가 쓴 것은 400, 같은 사람이 같은 대상을 또 신고하면 409."""
    if target_author_id == me.id:
        raise HTTPException(status_code=400, detail="내가 쓴 것은 신고할 수 없어요.")
    db.add(Report(reporter_id=me.id, target_type=target_type, target_id=target_id, reason=payload.reason, detail=payload.detail))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 신고했어요.") from None
    return MessageResponse(message="신고했어요. 관리자가 확인할게요.")
