"""알림 아이콘: 처리할 변경 요청, 소비기한 임박 식품, 내가 보낸 변경 요청의 결과, 내 글에 달린 댓글을 한곳에서 센다. 푸시 알림이 아니라 앱이 열려 있을 때 부르는 조회다."""

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import ChangeRequest, Comment, FoodItem, Fridge, FridgeMember, Post, User
from app.routers.change_requests import _out as change_request_out
from app.schemas import (
    ChangeResultList,
    ChangeResultOut,
    ChangeResultsRead,
    CommentNotificationList,
    CommentNotificationOut,
    CommentsRead,
    ExpiringItemList,
    ExpiringItemOut,
    NotificationSummary,
    PublicUser,
)
from app.services.expiry import label_of
from app.timeutil import today, utcnow

router = APIRouter(prefix="/notifications", tags=["알림"])

EXPIRY_ALERT_DAYS = 3  # TODO: balance. 소비기한이 이 일수 이내로 남은 식품(이미 지난 것 포함)을 알린다. 냉장고별 /items/expiring의 기본값과 같다


def _comments_on_my_posts(me: User) -> list:
    """다른 사람이 내 글에 단 댓글 (지운 글과 지운 댓글은 뺀다. 내가 단 댓글도 뺀다)."""
    return [Post.author_id == me.id, Post.deleted_at.is_(None), Comment.deleted_at.is_(None), Comment.author_id != me.id]


def _my_results(me: User) -> list:
    """내가 보낸 변경 요청 중 주인이 승인하거나 거절한 것 (내가 취소한 것과 친구가 끊겨 취소된 것은 결과가 아니다)."""
    return [ChangeRequest.requester_id == me.id, ChangeRequest.status.in_(("approved", "declined"))]


def _expiring(me: User) -> list:
    """내가 멤버(주인이나 고치기 권한)인 냉장고들의 남은 식품 중 소비기한이 임박했거나 지난 것. 보기만 하는 친구의 냉장고는 뺀다."""
    return [
        FridgeMember.user_id == me.id,
        FoodItem.status == "active",
        FoodItem.expiry_date <= today() + timedelta(days=EXPIRY_ALERT_DAYS),
    ]


def _expiring_count(db: Session, me: User) -> int:
    return db.scalar(
        select(func.count()).select_from(FoodItem).join(FridgeMember, FridgeMember.fridge_id == FoodItem.fridge_id).where(*_expiring(me))
    )


def _summary(db: Session, me: User) -> NotificationSummary:
    pending = db.scalar(
        select(func.count())
        .select_from(ChangeRequest)
        .join(Fridge, Fridge.id == ChangeRequest.fridge_id)
        .where(Fridge.owner_id == me.id, ChangeRequest.status == "pending")
    )
    unread = db.scalar(
        select(func.count())
        .select_from(Comment)
        .join(Post, Post.id == Comment.post_id)
        .where(*_comments_on_my_posts(me), Comment.id > me.comments_seen_id)
    )
    results = db.scalar(select(func.count()).select_from(ChangeRequest).where(*_my_results(me), ChangeRequest.result_seen_at.is_(None)))
    expiring = _expiring_count(db, me)
    return NotificationSummary(
        pending_change_requests=pending,
        expiring_items=expiring,
        unread_change_results=results,
        unread_comments=unread,
        total=pending + expiring + results + unread,
    )


@router.get("/summary", response_model=NotificationSummary, summary="아이콘에 붙일 숫자: 답하지 않은 변경 요청 수, 소비기한 임박 식품 수, 내 요청의 새 결과 수, 새 댓글 수")
def summary(me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _summary(db, me)


@router.get(
    "/comments",
    response_model=CommentNotificationList,
    summary="내 글에 달린 댓글 내용 (최신순). 다음 쪽은 before_id에 next_before_id를 넣어 부른다",
)
def list_comment_notifications(
    before_id: int | None = Query(None, ge=1, description="이 번호보다 오래된 댓글부터"),
    limit: int = Query(20, ge=1, le=50),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        select(Comment, Post, User)
        .join(Post, Post.id == Comment.post_id)
        .join(User, User.id == Comment.author_id)
        .where(*_comments_on_my_posts(me))
    )
    if before_id is not None:
        query = query.where(Comment.id < before_id)
    rows = db.execute(query.order_by(Comment.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    return CommentNotificationList(
        comments=[
            CommentNotificationOut(
                comment_id=comment.id,
                post_id=post.id,
                post_title=post.title,
                commenter=PublicUser.of(commenter),
                body=comment.body,
                created_at=comment.created_at,
                unread=comment.id > me.comments_seen_id,
            )
            for comment, post, commenter in page
        ],
        next_before_id=page[-1][0].id if len(rows) > limit else None,
        unread_count=_summary(db, me).unread_comments,
    )


@router.post("/comments/read", response_model=NotificationSummary, summary="댓글을 읽음으로 표시하고 새 숫자를 돌려준다 (up_to_id까지, 비우면 지금까지 모두)")
def mark_comments_read(payload: CommentsRead | None = None, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    latest = db.scalar(select(func.max(Comment.id))) or 0
    # 앞으로 달릴 댓글까지 읽음 처리되지 않게 지금 있는 가장 새 번호를 넘지 못한다
    upto = min(payload.up_to_id, latest) if payload and payload.up_to_id else latest
    # 번호는 올라가기만 한다 (오래된 화면에서 보낸 요청이 읽은 자리를 되돌리지 않게)
    db.execute(update(User).where(User.id == me.id, User.comments_seen_id < upto).values(comments_seen_id=upto))
    db.commit()
    db.refresh(me)
    return _summary(db, me)


@router.get(
    "/change-results",
    response_model=ChangeResultList,
    summary="내가 보낸 변경 요청의 결과(승인·거절) 목록 (최신순). 다음 쪽은 before_id에 next_before_id를 넣어 부른다",
)
def list_change_results(
    before_id: int | None = Query(None, ge=1, description="이 번호보다 오래된 요청부터"),
    limit: int = Query(20, ge=1, le=50),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(ChangeRequest).where(*_my_results(me))
    if before_id is not None:
        query = query.where(ChangeRequest.id < before_id)
    rows = db.scalars(query.order_by(ChangeRequest.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    owners = {user.id: user for user in db.scalars(select(User).where(User.id.in_({row.handled_by for row in page if row.handled_by}))).all()}
    return ChangeResultList(
        requests=[
            ChangeResultOut(
                **change_request_out(db, row).model_dump(),
                owner=PublicUser.of(owners[row.handled_by]) if row.handled_by in owners else None,
                unread=row.result_seen_at is None,
            )
            for row in page
        ],
        next_before_id=page[-1].id if len(rows) > limit else None,
        unread_count=_summary(db, me).unread_change_results,
    )


@router.post("/change-results/read", response_model=NotificationSummary, summary="변경 요청 결과를 확인함으로 표시하고 새 숫자를 돌려준다 (ids만, 비우면 지금까지 모두)")
def mark_change_results_read(payload: ChangeResultsRead | None = None, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = update(ChangeRequest).where(*_my_results(me), ChangeRequest.result_seen_at.is_(None)).values(result_seen_at=utcnow())
    if payload and payload.ids is not None:
        query = query.where(ChangeRequest.id.in_(payload.ids))
    db.execute(query)
    db.commit()
    return _summary(db, me)


@router.get(
    "/expiring",
    response_model=ExpiringItemList,
    summary=f"소비기한 임박 식품 (내가 멤버인 모든 냉장고에서 {EXPIRY_ALERT_DAYS}일 이내로 남았거나 이미 지난 것, 빠른 순)",
)
def list_expiring_items(limit: int = Query(50, ge=1, le=200), me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(FoodItem, Fridge)
        .join(FridgeMember, FridgeMember.fridge_id == FoodItem.fridge_id)
        .join(Fridge, Fridge.id == FoodItem.fridge_id)
        .where(*_expiring(me))
        .order_by(FoodItem.expiry_date, FoodItem.id)
        .limit(limit)
    ).all()
    return ExpiringItemList(
        items=[
            ExpiringItemOut(
                item_id=item.id,
                fridge_id=fridge.id,
                fridge_name=fridge.name,
                name=item.name,
                category=item.category,
                category_label=label_of(item.category),
                storage=item.storage,
                remaining_quantity=item.remaining_quantity,
                expiry_date=item.expiry_date,
            )
            for item, fridge in rows
        ],
        count=_expiring_count(db, me),
        alert_days=EXPIRY_ALERT_DAYS,
    )
