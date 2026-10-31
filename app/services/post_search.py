"""게시글 조회·검색. 검색 구현을 바꾸더라도 라우터와 앱의 응답 형식은 유지한다."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Post, User
from app.schemas import PostBoard


def find_posts(
    db: Session, *, query: str = "", board: PostBoard | None = None,
    before_id: int | None = None, limit: int = 20,
) -> list[tuple[Post, User]]:
    """제목·본문 부분 일치 검색. 다음 쪽 유무를 위해 limit + 1개를 반환한다.

    임베딩 검색을 추가할 때의 교체 지점이다. 삭제 제외·게시판 필터는 항상
    적용하고, 현재는 id 내림차순과 before_id를 한 쌍으로 사용한다.
    """
    statement = select(Post, User).join(User, User.id == Post.author_id).where(Post.deleted_at.is_(None))
    if board is not None:
        statement = statement.where(Post.board == board)
    keyword = query.strip()
    if keyword:
        statement = statement.where(or_(
            Post.title.icontains(keyword, autoescape=True),
            Post.body.icontains(keyword, autoescape=True),
        ))
    if before_id is not None:
        statement = statement.where(Post.id < before_id)
    return [(post, author) for post, author in db.execute(statement.order_by(Post.id.desc()).limit(limit + 1)).all()]
