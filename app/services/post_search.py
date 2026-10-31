"""게시글 조회·검색. 검색어가 없으면 최신순 목록, 있으면 키워드 + 임베딩 하이브리드 관련도순.

응답 형식(PostList)은 그대로이고 달라진 것은 `before_id`의 뜻뿐이다.
* 검색어 없음: 이 글 번호보다 오래된 글부터 (기존과 같다)
* 검색어 있음: 관련도순이라 글 번호로는 이어 붙일 수 없어서, "이미 받은 결과 수"를 돌려주고 그 다음부터 준다.
  앱은 받은 `next_before_id`를 그대로 되돌려 보낼 뿐이라 고치지 않아도 된다.

삭제 제외·게시판 필터는 두 경우 모두 항상 적용한다.
"""

import hashlib
import logging

from sqlalchemy import insert, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Post, PostEmbedding, User
from app.schemas import PostBoard
from app.services.embeddings import (
    Embedder,
    EmbeddingError,
    Vector,
    cosine,
    get_embedder,
    normalize,
    pack,
    unpack,
    words,
)
from app.timeutil import utcnow

log = logging.getLogger(__name__)

POOL_SIZE = 2000  # 한 번의 검색에서 살펴보는 최신 글 수. 더 오래된 글은 검색어 전체가 글자 그대로 들어 있을 때만 찾는다
INDEX_BATCH = 500  # 한 번의 검색에서 새로 만들어 저장하는 벡터 수의 상한. 넘는 글은 다음 검색들에서 채워진다
BODY_CHARS = 300  # 벡터를 만들 때 쓰는 본문 앞부분의 길이
MAX_TERMS = 8  # 키워드 점수에 쓰는 검색어 단어 수의 상한
LEXICAL_WEIGHT = 0.6  # 점수 = 키워드 점수 × 0.6 + 의미 점수 × 0.4
SEMANTIC_WEIGHT = 0.4


def document_text(title: str, body: str) -> str:
    """벡터로 바꿀 글의 모습. 제목을 두 번 넣어 제목이 더 크게 반영되게 한다."""
    return f"{title}\n{title}\n{body[:BODY_CHARS]}"


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def find_posts(
    db: Session, *, query: str = "", board: PostBoard | None = None,
    before_id: int | None = None, limit: int = 20,
) -> tuple[list[tuple[Post, User]], int | None]:
    """(이번 쪽의 글과 쓴 사람, 다음 쪽을 부를 next_before_id 또는 마지막이면 None)."""
    keyword = query.strip()
    if keyword:
        return _search(db, keyword, board, offset=before_id or 0, limit=limit)
    statement = select(Post, User).join(User, User.id == Post.author_id).where(Post.deleted_at.is_(None))
    if board is not None:
        statement = statement.where(Post.board == board)
    if before_id is not None:
        statement = statement.where(Post.id < before_id)
    rows = [(post, author) for post, author in db.execute(statement.order_by(Post.id.desc()).limit(limit + 1)).all()]
    page = rows[:limit]  # limit + 1개를 읽어 다음 쪽이 있는지 안다
    return page, (page[-1][0].id if len(rows) > limit else None)


# ── 검색 ─────────────────────────────────────────────────────────────────


def _search(db: Session, keyword: str, board: PostBoard | None, *, offset: int, limit: int) -> tuple[list[tuple[Post, User]], int | None]:
    phrase = normalize(keyword).strip()
    terms = list(dict.fromkeys(words(keyword)))[:MAX_TERMS]
    rows = _candidates(db, board, keyword)

    embedder = get_embedder()
    query_vector: Vector | None = None
    if embedder is not None:
        try:
            query_vector = embedder.embed_query(keyword)
        except EmbeddingError:
            log.warning("검색어 임베딩 실패 — 키워드 점수만 쓴다", exc_info=True)
    vectors = _document_vectors(db, embedder, rows) if embedder is not None and query_vector is not None else {}

    scored: list[tuple[float, int]] = []
    for row in rows:
        lexical = _lexical(phrase, terms, normalize(row.title), normalize(row.body))
        vector = vectors.get(row.id)
        semantic = _semantic(embedder, query_vector, vector) if embedder is not None and query_vector is not None and vector is not None else 0.0
        if lexical or semantic:
            scored.append((LEXICAL_WEIGHT * lexical + SEMANTIC_WEIGHT * semantic, row.id))
    scored.sort(key=lambda item: (-item[0], -item[1]))  # 점수가 같으면 최신 글이 먼저

    ids = [post_id for _, post_id in scored[offset:offset + limit]]
    next_cursor = offset + limit if len(scored) > offset + limit else None
    if not ids:
        return [], None
    loaded = {
        post.id: (post, author)
        for post, author in db.execute(
            select(Post, User).join(User, User.id == Post.author_id).where(Post.id.in_(ids), Post.deleted_at.is_(None))
        ).all()
    }
    return [loaded[post_id] for post_id in ids if post_id in loaded], next_cursor


def _candidate_select(board: PostBoard | None):
    statement = (
        select(Post.id, Post.title, Post.body, PostEmbedding.model, PostEmbedding.content_hash, PostEmbedding.vector)
        .outerjoin(PostEmbedding, PostEmbedding.post_id == Post.id)
        .where(Post.deleted_at.is_(None))
    )
    return statement if board is None else statement.where(Post.board == board)


def _candidates(db: Session, board: PostBoard | None, keyword: str):
    """점수를 매길 글: 최신 POOL_SIZE개. 더 오래된 글이 남아 있으면 검색어 전체가 글자 그대로 들어 있는 것만 더한다."""
    rows = db.execute(_candidate_select(board).order_by(Post.id.desc()).limit(POOL_SIZE)).all()
    if len(rows) == POOL_SIZE:
        older = (
            _candidate_select(board)
            .where(
                Post.id < rows[-1].id,
                or_(Post.title.icontains(keyword, autoescape=True), Post.body.icontains(keyword, autoescape=True)),
            )
            .order_by(Post.id.desc())
            .limit(POOL_SIZE)
        )
        rows = rows + db.execute(older).all()
    return rows


def _lexical(phrase: str, terms: list[str], title: str, body: str) -> float:
    """키워드 점수 0~1. 검색어 전체가 제목에 있으면 1, 본문에 있으면 0.85, 아니면 단어별로 (제목 1, 본문 0.7) 맞은 비율 × 0.9."""
    if phrase in title:
        return 1.0
    score = 0.85 if phrase in body else 0.0
    if terms:
        hits = sum(1.0 if term in title else 0.7 if term in body else 0.0 for term in terms)
        score = max(score, 0.9 * hits / len(terms))
    return score


def _semantic(embedder: Embedder, query_vector: Vector, vector: Vector) -> float:
    """의미 점수 0~1. 코사인 유사도가 제공자의 하한(min_similarity) 아래면 0, 위로 올라갈수록 1에 가까워진다."""
    floor = embedder.min_similarity
    similarity = cosine(query_vector, vector)
    return 0.0 if similarity < floor else min(1.0, (similarity - floor) / (1 - floor))


# ── 벡터 색인 (검색할 때 없거나 낡은 것만 다시 만든다) ──────────────────────


def _document_vectors(db: Session, embedder: Embedder, rows) -> dict[int, Vector]:
    vectors: dict[int, Vector] = {}
    stale: list[tuple[int, bool, str, str]] = []  # (글 번호, 저장된 행이 있는지, 내용 해시, 벡터로 바꿀 글)
    for row in rows:
        text = document_text(row.title, row.body)
        digest = _hash(text)
        if row.vector is not None and row.model == embedder.name and row.content_hash == digest:
            vectors[row.id] = unpack(row.vector)
        else:
            stale.append((row.id, row.vector is not None, digest, text))
    if not stale:
        return vectors
    batch = stale[:INDEX_BATCH]
    try:
        made = embedder.embed_documents([text for _, _, _, text in batch])
    except EmbeddingError:
        log.warning("글 임베딩 실패 — 이번 검색은 이 글들의 키워드 점수만 쓴다", exc_info=True)
        return vectors
    fresh = [(post_id, had_row, digest, vector) for (post_id, had_row, digest, _), vector in zip(batch, made)]
    for post_id, _, _, vector in fresh:
        vectors[post_id] = vector
    _store(db, embedder.name, fresh)
    return vectors


def _store(db: Session, model: str, fresh: list[tuple[int, bool, str, Vector]]) -> None:
    """만든 벡터를 저장한다. 실패해도(같은 글을 동시에 저장하는 등) 이번 검색 결과에는 영향이 없고, 다음 검색이 다시 시도한다."""
    now = utcnow()

    def values(post_id: int, digest: str, vector: Vector) -> dict:
        return {"post_id": post_id, "model": model, "content_hash": digest, "vector": pack(vector), "updated_at": now}

    new_rows = [values(post_id, digest, vector) for post_id, had_row, digest, vector in fresh if not had_row]
    old_rows = [values(post_id, digest, vector) for post_id, had_row, digest, vector in fresh if had_row]
    try:
        if new_rows:
            db.execute(insert(PostEmbedding), new_rows)
        if old_rows:
            db.execute(update(PostEmbedding), old_rows)  # 기본 키(post_id)로 찾아 고친다
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        log.warning("글 임베딩 저장 실패 — 다음 검색에서 다시 시도한다", exc_info=True)
