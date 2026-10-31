"""관리자: 신고된 글·댓글·레시피 후기를 보고 지우거나 문제없음으로 닫는다."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_admin
from app.models import Comment, Post, PostPhoto, RecipeReview, Report, User
from app.schemas import AdminReportOut, PublicUser, ReportResolve, ReportTargetOut
from app.services.community import delete_comment, delete_post, delete_review
from app.timeutil import utcnow

router = APIRouter(prefix="/admin", tags=["관리자"], dependencies=[Depends(require_admin)])

EXCERPT = 200


def _target(db: Session, report: Report) -> ReportTargetOut | None:
    if report.target_type == "post":
        post = db.get(Post, report.target_id)
        author = db.get(User, post.author_id) if post else None
        if post is None or author is None:
            return None
        photos = db.scalar(select(func.count()).select_from(PostPhoto).where(PostPhoto.post_id == post.id))
        return ReportTargetOut(
            post_id=post.id,
            author=PublicUser.of(author),
            text=f"{post.title}\n{post.body}"[:EXCERPT],
            photo_count=photos,
            deleted=post.deleted_at is not None,
        )
    if report.target_type == "review":
        review = db.get(RecipeReview, report.target_id)
        author = db.get(User, review.author_id) if review else None
        if review is None or author is None:
            return None
        return ReportTargetOut(
            recipe_id=review.recipe_id,
            recipe_name=review.recipe_name,
            author=PublicUser.of(author),
            text=f"별점 {review.rating}\n{review.body or ''}"[:EXCERPT],
            photo_count=0,
            deleted=review.deleted_at is not None,
        )
    comment = db.get(Comment, report.target_id)
    author = db.get(User, comment.author_id) if comment else None
    if comment is None or author is None:
        return None
    return ReportTargetOut(
        post_id=comment.post_id,
        author=PublicUser.of(author),
        text=comment.body[:EXCERPT],
        photo_count=0,
        deleted=comment.deleted_at is not None,
    )


def _out(db: Session, report: Report) -> AdminReportOut:
    reporter = db.get(User, report.reporter_id)
    open_count = db.scalar(
        select(func.count())
        .select_from(Report)
        .where(Report.target_type == report.target_type, Report.target_id == report.target_id, Report.status == "open")
    )
    return AdminReportOut(
        id=report.id,
        target_type=report.target_type,
        target_id=report.target_id,
        reason=report.reason,
        detail=report.detail,
        status=report.status,
        created_at=report.created_at,
        reporter=PublicUser.of(reporter),
        open_reports_on_target=open_count,
        target=_target(db, report),
    )


@router.get("/reports", response_model=list[AdminReportOut], summary="신고 목록 (기본은 아직 처리하지 않은 것, 오래된 것부터)")
def list_reports(
    status: Literal["open", "resolved", "dismissed", "all"] = Query("open"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = select(Report).order_by(Report.id).limit(limit)
    if status != "all":
        query = query.where(Report.status == status)
    return [_out(db, report) for report in db.scalars(query).all()]


@router.post("/reports/{report_id}/resolve", response_model=AdminReportOut, summary="신고 처리: delete(대상을 지움) / dismiss(문제없음)")
def resolve_report(report_id: int, payload: ReportResolve, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="신고를 찾을 수 없어요.")
    if report.status != "open":
        raise HTTPException(status_code=409, detail="이미 처리한 신고예요.")
    if payload.action == "delete":
        model, delete = {"post": (Post, delete_post), "comment": (Comment, delete_comment), "review": (RecipeReview, delete_review)}[report.target_type]
        target = db.get(model, report.target_id)
        if target is not None and target.deleted_at is None:
            delete(db, target, admin.id)
    # 같은 대상에 들어온 다른 신고도 같은 결과로 닫는다 (한 번 보고 정하면 된다)
    status = "resolved" if payload.action == "delete" else "dismissed"
    for other in db.scalars(
        select(Report).where(Report.target_type == report.target_type, Report.target_id == report.target_id, Report.status == "open")
    ).all():
        other.status, other.handled_by, other.handled_at = status, admin.id, utcnow()
    db.commit()
    return _out(db, report)
