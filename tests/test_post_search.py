"""커뮤니티 글 검색: 키워드 + 임베딩 하이브리드 관련도순, 벡터 색인(검색할 때 만든다), 장애 시 키워드 점수로 대체."""

import math
from array import array

import pytest
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import PostEmbedding
from app.services import embeddings, post_search
from app.services.embeddings import EmbeddingError, LocalNgramEmbedder, cosine, normalize, pack, unpack
from tests.helpers import auth, signup


@pytest.fixture
def me(client):
    tokens = signup(client, "minsu")
    return {"headers": auth(tokens), "id": tokens["user"]["id"]}


def write(client, me, title, body="내용", board="general"):
    response = client.post("/posts", headers=me["headers"], json={"title": title, "body": body, "board": board})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def search(client, me, q, **params):
    response = client.get("/posts", headers=me["headers"], params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


def ids(result):
    return [post["id"] for post in result["posts"]]


def embedding_rows():
    with SessionLocal() as db:
        return {row.post_id: row for row in db.scalars(select(PostEmbedding)).all()}


# ── 하이브리드 점수 ──────────────────────────────────────────────────────


def test_words_in_any_order_match_and_better_matches_rank_first(client, me):
    both = write(client, me, "달걀 요리", "저녁에 후기를 남겨요")  # 한 단어는 제목, 한 단어는 본문
    only_one = write(client, me, "아침 메뉴", "달걀을 익혀요")
    write(client, me, "두부 조림", "간장으로 조려요")
    # 예전에는 '달걀 후기' 전체가 글자 그대로 들어 있어야 했다. 이제는 단어별로 맞춰 보고, 많이 맞을수록 앞에 둔다.
    assert ids(search(client, me, "달걀 후기")) == [both, only_one]


def test_food_aliases_and_word_endings_are_matched(client, me):
    match = write(client, me, "달걀말이", "팬에 부쳐요")
    write(client, me, "두부 조림", "간장으로 조려요")
    assert ids(search(client, me, "계란말이")) == [match]  # 계란 = 달걀 (글과 검색어를 같은 이름으로 맞춘다)
    # '달걀을'은 글자 그대로는 없지만 글자 묶음(^달·달걀·^달걀)이 겹쳐 벡터가 가깝다 (조사·어미). 짧은 글에서 잘 맞고, 긴 글일수록 희석된다.
    assert ids(search(client, me, "달걀을")) == [match]


def test_pages_follow_relevance_without_gaps_or_duplicates(client, me):
    title_ids = [write(client, me, f"달걀 요리 {n}", "내용") for n in range(5)]
    body_ids = [write(client, me, f"메뉴 {n}", "오늘은 달걀을 먹었어요") for n in range(4)]
    seen, cursors, cursor = [], [], None
    while True:
        page = search(client, me, "달걀", limit=2, **({"before_id": cursor} if cursor else {}))
        seen += ids(page)
        cursor = page["next_before_id"]
        if cursor is None:
            break
        cursors.append(cursor)
    assert cursors == [2, 4, 6, 8]  # 다음 쪽 표시는 이미 받은 결과 수
    assert len(seen) == len(set(seen)) == 9
    assert set(seen[:5]) == set(title_ids) and set(seen[5:]) == set(body_ids)  # 제목에 있는 글이 모두 본문에만 있는 글보다 앞
    assert search(client, me, "달걀", before_id=100) == {"posts": [], "next_before_id": None}


def test_semantic_score_adds_matches_that_keywords_miss(client, me, monkeypatch):
    class TopicEmbedder:
        """'냉동·얼리'가 들어간 글은 한 방향, 나머지는 직각 방향으로 보내는 가짜 의미 모델."""

        name = "topic:2"
        min_similarity = 0.5

        def _vector(self, text):
            return array("f", [1.0, 0.0] if "냉동" in text or "얼리" in text else [0.0, 1.0])

        def embed_documents(self, texts):
            return [self._vector(text) for text in texts]

        def embed_query(self, text):
            return self._vector(text)

    monkeypatch.setattr(post_search, "get_embedder", lambda: TopicEmbedder())
    semantic_only = write(client, me, "냉동 보관법", "소분해서 두세요")  # '얼리기'라는 글자는 없지만 뜻이 가깝다
    keyword_hit = write(client, me, "얼리기 꿀팁", "내용")
    write(client, me, "두부 조림", "간장으로 조려요")
    assert ids(search(client, me, "얼리기")) == [keyword_hit, semantic_only]  # 키워드도 맞으면 더 앞, 무관한 글은 빠진다
    assert {row.model for row in embedding_rows().values()} == {"topic:2"}


def test_old_posts_beyond_the_pool_are_found_when_they_contain_the_phrase(client, me, monkeypatch):
    monkeypatch.setattr(post_search, "POOL_SIZE", 3)
    old_match = write(client, me, "달걀 비법", "오래된 글")
    write(client, me, "두부 이야기", "오래된 글")
    for n in range(3):  # 최신 3개가 살펴보는 범위를 채운다
        write(client, me, f"요즘 글 {n}", "내용")
    assert ids(search(client, me, "달걀")) == [old_match]


# ── 벡터 색인 ────────────────────────────────────────────────────────────


def test_vectors_are_created_when_searching_and_refreshed_after_an_edit(client, me):
    first = write(client, me, "달걀말이", "팬에 부쳐요")
    second = write(client, me, "두부 조림", "간장으로 조려요")
    assert embedding_rows() == {}  # 글을 쓸 때가 아니라 검색할 때 만든다
    client.get("/posts", headers=me["headers"])
    search(client, me, "   ")
    assert embedding_rows() == {}  # 검색어가 없으면 만들지 않는다

    search(client, me, "달걀")
    rows = embedding_rows()
    assert set(rows) == {first, second}
    assert {row.model for row in rows.values()} == {LocalNgramEmbedder.name}
    assert all(len(unpack(row.vector)) == LocalNgramEmbedder.DIM for row in rows.values())

    search(client, me, "달걀")
    assert {post_id: row.content_hash for post_id, row in embedding_rows().items()} == {post_id: row.content_hash for post_id, row in rows.items()}

    assert client.patch(f"/posts/{first}", headers=me["headers"], json={"title": "순두부찌개"}).status_code == 200
    assert ids(search(client, me, "순두부"))[0] == first  # 고친 제목이 바로 반영된다 ('두부 조림'도 글자가 겹쳐 뒤따라올 수 있다)
    after = embedding_rows()
    assert after[first].content_hash != rows[first].content_hash and after[first].vector != rows[first].vector  # 고친 글만 다시 만든다
    assert after[second].content_hash == rows[second].content_hash and after[second].vector == rows[second].vector
    assert ids(search(client, me, "달걀말이")) == []  # 옛 제목으로는 더 이상 나오지 않는다

    assert client.delete(f"/posts/{second}", headers=me["headers"]).status_code == 204
    assert ids(search(client, me, "두부")) == [first]  # 지운 글은 벡터가 남아 있어도 나오지 않는다 (순두부찌개는 '두부'를 포함한다)
    assert ids(search(client, me, "간장")) == []


def test_vectors_made_by_another_method_are_replaced(client, me, monkeypatch):
    post = write(client, me, "달걀말이", "팬에 부쳐요")
    search(client, me, "달걀")
    assert embedding_rows()[post].model == LocalNgramEmbedder.name
    monkeypatch.setattr(embeddings._LOCAL, "name", "local-ngram-v2:512:test")
    assert ids(search(client, me, "달걀")) == [post]
    assert embedding_rows()[post].model == "local-ngram-v2:512:test"


def test_index_batch_limits_the_work_done_by_one_search(client, me, monkeypatch):
    monkeypatch.setattr(post_search, "INDEX_BATCH", 2)
    for n in range(5):
        write(client, me, f"달걀 요리 {n}")
    counts = []
    for _ in range(3):
        assert len(search(client, me, "달걀", limit=50)["posts"]) == 5  # 아직 벡터가 없는 글도 키워드로는 찾는다
        counts.append(len(embedding_rows()))
    assert counts == [2, 4, 5]


# ── 끄거나 고장 났을 때 ──────────────────────────────────────────────────


def test_search_still_works_with_the_embedding_provider_off(client, me, monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "none")
    both = write(client, me, "달걀 요리", "저녁에 후기를 남겨요")
    write(client, me, "두부 조림", "간장으로 조려요")
    assert ids(search(client, me, "달걀 후기")) == [both]
    assert ids(search(client, me, "달걀을")) == []  # 의미 점수가 없으니 글자 그대로 맞는 것만
    assert embedding_rows() == {}


@pytest.mark.parametrize("failing", ["query", "documents", "both"])
def test_embedding_errors_fall_back_to_keyword_scores(client, me, monkeypatch, failing):
    class Broken:
        name = "broken:1"
        min_similarity = 0.5

        def embed_documents(self, texts):
            if failing in ("documents", "both"):
                raise EmbeddingError("서버가 응답하지 않아요")
            return [array("f", [1.0, 0.0]) for _ in texts]

        def embed_query(self, text):
            if failing in ("query", "both"):
                raise EmbeddingError("서버가 응답하지 않아요")
            return array("f", [1.0, 0.0])

    monkeypatch.setattr(post_search, "get_embedder", lambda: Broken())
    match = write(client, me, "달걀 요리", "내용")
    write(client, me, "두부 조림", "간장으로 조려요")
    result = client.get("/posts", headers=me["headers"], params={"q": "달걀"})
    assert result.status_code == 200
    assert ids(result.json()) == [match]
    assert embedding_rows() == {}  # 검색어나 글의 벡터를 못 만들었으면 아무것도 저장하지 않는다


def test_a_failed_save_does_not_break_the_search(client, me, monkeypatch):
    def refuse(*args, **kwargs):
        raise post_search.SQLAlchemyError("저장 실패")

    monkeypatch.setattr(post_search, "insert", refuse)
    match = write(client, me, "달걀말이", "팬에 부쳐요")
    assert ids(search(client, me, "달걀을")) == [match]  # 방금 만든 벡터로 이번 검색은 정상 응답한다
    assert embedding_rows() == {}


# ── 임베딩 자체 ──────────────────────────────────────────────────────────


def test_local_embedding_is_unit_length_deterministic_and_packs_losslessly():
    embedder = LocalNgramEmbedder()
    vector = embedder.embed_query("달걀말이 만드는 법")
    assert len(vector) == LocalNgramEmbedder.DIM
    assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, rel_tol=1e-5)
    assert list(embedder.embed_documents(["달걀말이 만드는 법"])[0]) == list(vector)
    assert list(unpack(pack(vector))) == list(vector)
    assert math.isclose(cosine(vector, vector), 1.0, rel_tol=1e-5)
    assert not any(embedder.embed_query("!!! ???"))  # 글자가 없으면 영벡터 → 어떤 글과도 유사도 0
    assert cosine(vector, array("f", [1.0])) == 0.0  # 길이가 다르면(다른 방식으로 만든 것) 비교하지 않는다


def test_local_embedding_keeps_related_words_closer_than_unrelated_ones():
    embedder = LocalNgramEmbedder()
    egg = embedder.embed_query("달걀말이")
    assert cosine(egg, embedder.embed_query("달걀을")) > embedder.min_similarity
    assert cosine(egg, embedder.embed_query("두부조림")) < embedder.min_similarity
    assert math.isclose(cosine(egg, embedder.embed_query("계란말이")), 1.0, rel_tol=1e-5)  # 별칭을 맞춘 뒤 비교한다


def test_local_embedding_algorithm_is_pinned():
    """알고리즘(글자 묶음·가중치·해시·차원)을 바꾸면 이 값이 달라진다. 그때는 LocalNgramEmbedder.name의 버전(v1)도 올려야
    이미 저장된 벡터가 새 방식으로 다시 만들어진다. (실행 환경의 부동소수점 끝자리 차이를 피하려고 반올림한 값으로 비교한다.)"""
    vector = LocalNgramEmbedder().embed_query("달걀말이 만드는 법")
    top = sorted(range(len(vector)), key=lambda i: (-abs(vector[i]), i))[:5]
    assert [(i, round(vector[i], 3)) for i in top] == [(242, 0.296), (264, -0.296), (439, 0.296), (16, -0.197), (37, -0.197)]
    assert sum(1 for x in vector if x) == 22


def test_normalize_unifies_case_width_and_food_aliases():
    assert normalize("계란 쇠고기 야채 ABC ＡＢＣ") == "달걀 소고기 채소 abc abc"
