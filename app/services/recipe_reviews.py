"""레시피 별점 모으기. 지운 후기는 평균에도 개수에도 들어가지 않는다."""

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Post, RecipeReview
from app.schemas import RatingSummary


def sync_review_post(db: Session, review: RecipeReview) -> None:
    """후기 저장과 같은 트랜잭션에서 게시글 하나를 생성하거나 갱신한다."""
    db.flush()
    post = db.scalar(select(Post).where(Post.review_id == review.id))
    if post is None:
        post = Post(author_id=review.author_id, review_id=review.id, board="recipe", created_at=review.created_at)
        db.add(post)
    post.title = f"{review.recipe_name[:90]} 요리 후기"
    post.body = review.body or f"별점 {review.rating}점을 남겼어요."
    post.updated_at = review.updated_at


def rating_summaries(db: Session, recipe_ids: Iterable[str]) -> dict[str, RatingSummary]:
    """레시피마다 평균, 개수, 별점별 분포를 한꺼번에 구한다 (레시피마다 따로 묻지 않는다). 후기가 없는 레시피도 키가 있다."""
    ids = set(recipe_ids)
    counts: dict[str, dict[int, int]] = {recipe_id: {} for recipe_id in ids}
    if ids:
        rows = db.execute(
            select(RecipeReview.recipe_id, RecipeReview.rating, func.count())
            .where(RecipeReview.recipe_id.in_(ids), RecipeReview.deleted_at.is_(None))
            .group_by(RecipeReview.recipe_id, RecipeReview.rating)
        ).all()
        for recipe_id, rating, count in rows:
            counts[recipe_id][rating] = count
    return {recipe_id: _summary(per_rating) for recipe_id, per_rating in counts.items()}


def _summary(per_rating: dict[int, int]) -> RatingSummary:
    total = sum(per_rating.values())
    average = round(sum(rating * count for rating, count in per_rating.items()) / total, 1) if total else None
    return RatingSummary(average=average, count=total, distribution={str(star): per_rating.get(star, 0) for star in range(1, 6)})
