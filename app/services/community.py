"""커뮤니티 글·댓글 지우기. 글쓴이나 관리자가 지울 때와, 관리자가 신고를 처리할 때 같은 규칙을 쓴다."""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Comment, Post, PostPhoto
from app.timeutil import utcnow


def remove_photo_file(photo: PostPhoto) -> None:
    (Path(settings.upload_dir) / photo.path).unlink(missing_ok=True)


def delete_post(db: Session, post: Post, by_user_id: int) -> None:
    """글을 지운 것으로 표시하고(누가 지웠는지 남긴다) 붙인 사진은 파일째 지운다. 댓글은 글과 함께 안 보이게 된다."""
    post.deleted_at, post.deleted_by = utcnow(), by_user_id
    for photo in db.scalars(select(PostPhoto).where(PostPhoto.post_id == post.id)).all():
        remove_photo_file(photo)
        db.delete(photo)
    db.commit()


def delete_comment(db: Session, comment: Comment, by_user_id: int) -> None:
    comment.deleted_at, comment.deleted_by = utcnow(), by_user_id
    db.commit()
