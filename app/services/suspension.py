"""커뮤니티 임시 정지: 관리자가 걸고 풀고, 글·댓글·후기를 쓰기 전에 막는다.

정지된 사람도 읽기, 좋아요, 신고, 내 글 지우기는 할 수 있다. 막는 것은 새 글·댓글·후기·사진 올리기와 글 고치기뿐이다.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CommunitySuspension, User
from app.timeutil import utcnow


def _active(user_id: int, now: datetime):
    return select(CommunitySuspension).where(
        CommunitySuspension.user_id == user_id, CommunitySuspension.lifted_at.is_(None), CommunitySuspension.ends_at > now
    )


def active_suspension(db: Session, user_id: int) -> CommunitySuspension | None:
    """지금 정지 중이면 그 기록(끝나는 때가 가장 늦은 것), 아니면 None."""
    return db.scalar(_active(user_id, utcnow()).order_by(CommunitySuspension.ends_at.desc()).limit(1))


def status_of(suspension: CommunitySuspension, now: datetime | None = None) -> str:
    if suspension.lifted_at is not None:
        return "lifted"
    return "active" if suspension.ends_at > (now or utcnow()) else "ended"


def local_time(value: datetime) -> str:
    """DB의 UTC 시각을 서비스 시간대(기본 Asia/Seoul)의 '2026-11-07 14:30'으로."""
    return value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M")


def ensure_can_write(db: Session, user: User) -> None:
    """글·댓글·후기를 쓰기 전에 부른다. 정지 중이면 끝나는 때와 사유를 담아 403."""
    suspension = active_suspension(db, user.id)
    if suspension is not None:
        raise HTTPException(
            status_code=403,
            detail=f"커뮤니티 이용이 임시 정지된 계정이에요. {local_time(suspension.ends_at)}까지 글, 댓글, 후기를 쓸 수 없어요.\n사유: {suspension.reason}",
        )


def suspend(db: Session, admin: User, target: User, days: int, reason: str) -> CommunitySuspension:
    """지금부터 days일 동안 정지한다. 이미 정지 중이면 이전 것을 풀고 새로 건다(기간을 줄이거나 늘릴 수 있다)."""
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="자기 자신은 정지할 수 없어요.")
    if target.is_admin:
        raise HTTPException(status_code=400, detail="관리자는 정지할 수 없어요.")
    now = utcnow()
    for earlier in db.scalars(_active(target.id, now)).all():
        earlier.lifted_at, earlier.lifted_by = now, admin.id
    record = CommunitySuspension(user_id=target.id, created_by=admin.id, reason=reason, created_at=now, ends_at=now + timedelta(days=days))
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def lift(db: Session, admin: User, target: User) -> int:
    """지금 걸린 정지를 모두 푼다. 푼 개수를 돌려준다 (정지 중이 아니었으면 0)."""
    now = utcnow()
    current = db.scalars(_active(target.id, now)).all()
    for suspension in current:
        suspension.lifted_at, suspension.lifted_by = now, admin.id
    db.commit()
    return len(current)
