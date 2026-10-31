"""레시피 별점과 후기. 레시피 자체에 남기는 것이라 냉장고와 상관없이 로그인한 모두가 쓰고 본다."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import RecipeReview, User
from app.schemas import MessageResponse, MyReviewList, PublicUser, ReportCreate, ReviewList, ReviewOut, ReviewWrite
from app.services import recipe_store
from app.services.community import delete_review, file_report
from app.services.recipe_reviews import rating_summaries, sync_review_post
from app.timeutil import utcnow

router = APIRouter(prefix="/recipes", tags=["레시피 후기"])

REVIEWS_PER_HOUR = 20  # TODO: balance. 지운 후기도 센다 (지우고 다시 써서 도배하는 것을 막는다)
RECIPE_NOT_FOUND = "레시피를 찾을 수 없어요."
REVIEW_NOT_FOUND = "후기를 찾을 수 없어요."


def _out(review: RecipeReview, author: User, me: User) -> ReviewOut:
    return ReviewOut(
        id=review.id,
        recipe_id=review.recipe_id,
        recipe_name=review.recipe_name,
        author=PublicUser.of(author),
        rating=review.rating,
        body=review.body,
        created_at=review.created_at,
        updated_at=review.updated_at,
        mine=review.author_id == me.id,
        can_delete=review.author_id == me.id or me.is_admin,
    )


def _live_review(db: Session, recipe_id: str, review_id: int) -> tuple[RecipeReview, User]:
    row = db.execute(
        select(RecipeReview, User)
        .join(User, User.id == RecipeReview.author_id)
        .where(RecipeReview.id == review_id, RecipeReview.recipe_id == recipe_id, RecipeReview.deleted_at.is_(None))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=REVIEW_NOT_FOUND)
    return row


def _my_live_review(db: Session, recipe_id: str, me: User) -> RecipeReview | None:
    return db.scalar(
        select(RecipeReview).where(RecipeReview.recipe_id == recipe_id, RecipeReview.author_id == me.id, RecipeReview.deleted_at.is_(None))
    )


def _recipe_name(recipe_id: str) -> str:
    recipe = recipe_store.detail(recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail=RECIPE_NOT_FOUND)
    return (recipe.get("display_name") or recipe["name"])[:200]


@router.get("/reviews/mine", response_model=MyReviewList, summary="내가 쓴 후기 모아 보기 (최신순). 다음 쪽은 before_id에 next_before_id를 넣어 부른다")
def list_my_reviews(
    before_id: int | None = Query(None, ge=1, description="이 번호보다 오래된 후기부터"),
    limit: int = Query(20, ge=1, le=50),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(RecipeReview).where(RecipeReview.author_id == me.id, RecipeReview.deleted_at.is_(None))
    if before_id is not None:
        query = query.where(RecipeReview.id < before_id)
    rows = db.scalars(query.order_by(RecipeReview.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    return MyReviewList(reviews=[_out(review, me, me) for review in page], next_before_id=page[-1].id if len(rows) > limit else None)


@router.get("/{recipe_id}/reviews", response_model=ReviewList, summary="레시피의 별점 요약과 후기 목록 (최신순). 다음 쪽은 before_id에 next_before_id를 넣어 부른다")
def list_reviews(
    recipe_id: str,
    before_id: int | None = Query(None, ge=1, description="이 번호보다 오래된 후기부터"),
    limit: int = Query(20, ge=1, le=50),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        select(RecipeReview, User)
        .join(User, User.id == RecipeReview.author_id)
        .where(RecipeReview.recipe_id == recipe_id, RecipeReview.deleted_at.is_(None))
    )
    if before_id is not None:
        query = query.where(RecipeReview.id < before_id)
    rows = db.execute(query.order_by(RecipeReview.id.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    mine = _my_live_review(db, recipe_id, me)
    return ReviewList(
        summary=rating_summaries(db, [recipe_id])[recipe_id],
        my_review=_out(mine, me, me) if mine else None,
        reviews=[_out(review, author, me) for review, author in page],
        next_before_id=page[-1][0].id if len(rows) > limit else None,
    )


@router.put(
    "/{recipe_id}/reviews/mine",
    response_model=ReviewOut,
    summary="별점과 후기 남기기. 이미 썼으면 고친다 (처음이면 201, 고치면 200)",
)
def write_review(recipe_id: str, payload: ReviewWrite, response: Response, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    name = _recipe_name(recipe_id)
    review = _my_live_review(db, recipe_id, me)
    if review is None:
        recent = db.scalar(
            select(func.count()).select_from(RecipeReview).where(RecipeReview.author_id == me.id, RecipeReview.created_at >= utcnow() - timedelta(hours=1))
        )
        if recent >= REVIEWS_PER_HOUR:
            raise HTTPException(
                status_code=429,
                detail=f"후기는 한 시간에 {REVIEWS_PER_HOUR}개까지 쓸 수 있어요. 잠시 후에 다시 써 주세요.",
                headers={"Retry-After": "600"},
            )
        review = RecipeReview(recipe_id=recipe_id, recipe_name=name, author_id=me.id, rating=payload.rating, body=payload.body)
        db.add(review)
        response.status_code = 201
    else:
        if (review.rating, review.body) != (payload.rating, payload.body):
            review.rating, review.body, review.updated_at = payload.rating, payload.body, utcnow()
        review.recipe_name = name
    try:
        sync_review_post(db, review)
        db.commit()
    except IntegrityError:  # 같은 사람이 같은 레시피에 동시에 두 번 보냈다
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 후기를 썼어요. 다시 불러와서 고쳐 주세요.") from None
    return _out(review, me, me)


@router.delete("/{recipe_id}/reviews/{review_id}", status_code=204, summary="후기 지우기 (쓴 사람 또는 관리자)")
def remove_review(recipe_id: str, review_id: int, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    review, _ = _live_review(db, recipe_id, review_id)
    if review.author_id != me.id and not me.is_admin:
        raise HTTPException(status_code=403, detail="내가 쓴 후기만 지울 수 있어요.")
    delete_review(db, review, me.id)
    return Response(status_code=204)


@router.post("/{recipe_id}/reviews/{review_id}/report", response_model=MessageResponse, status_code=201, summary="후기 신고하기")
def report_review(recipe_id: str, review_id: int, payload: ReportCreate, me: User = Depends(get_current_user), db: Session = Depends(get_db)):
    review, _ = _live_review(db, recipe_id, review_id)
    return file_report(db, me, "review", review.id, review.author_id, payload)
