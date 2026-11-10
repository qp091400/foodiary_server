"""게시글 검색용 임베딩(글을 숫자 벡터로 바꾸는 방식).

지금 제공자는 `local` 하나다. 키·네트워크·모델 파일 없이 서버 안에서 만든다.
글을 단어로 나누고, 단어와 그 안의 글자 2~3개 묶음(n-gram)을 고정 길이(512) 벡터에 해시로 섞은 뒤 길이를 1로 맞춘다.
그래서 두 글이 비슷한 글자 조합을 많이 쓰면 코사인 유사도가 커진다.

* 잘하는 것: 조사·어미가 달라진 말(달걀 / 달걀을 / 달걀말이), 일부만 맞는 단어, 흔한 오타.
* 못하는 것: 글자가 전혀 다른 동의어와 문장 뜻 이해(예: 계란 ↔ 달걀은 아래 FOOD_ALIASES로 따로 맞춘 것뿐이다).
  이런 의미 검색은 신경망 임베딩 제공자를 `Embedder` 모양에 맞춰 하나 더 만들면 된다. 저장·검색·정렬 쪽은 바꾸지 않아도 된다.

벡터는 `Embedder.name`과 함께 저장한다. 이름이 바뀌면(알고리즘·차원·별칭표 변경) 저장된 벡터는 낡은 것으로 보고 다시 만든다.
"""

import hashlib
import math
import re
import unicodedata
import zlib
from array import array
from collections import Counter
from collections.abc import Sequence
from typing import Protocol

from app.config import settings

Vector = array  # array("f"): 길이 1로 맞춘 float32 벡터


class EmbeddingError(RuntimeError):
    """임베딩을 만들지 못했다. 검색은 이 오류를 삼키고 키워드 점수만으로 돌아간다."""


class Embedder(Protocol):
    name: str  # 이 벡터를 만든 방식. 저장할 때 같이 적고, 다르면 다시 만든다
    min_similarity: float  # 코사인 유사도가 이 값보다 작으면 "관련 없음"으로 본다

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]: ...

    def embed_query(self, text: str) -> Vector: ...


# 자주 쓰는 식재료의 다른 이름. 글과 검색어를 같은 이름으로 맞춘 뒤 비교한다.
# 표를 고치면 아래 LocalNgramEmbedder.name이 바뀌어 저장된 벡터가 자동으로 다시 만들어진다.
FOOD_ALIASES = {"계란": "달걀", "쇠고기": "소고기", "야채": "채소"}

_WORD = re.compile(r"[0-9a-z가-힣]+")


def normalize(text: str) -> str:
    """같은 글자는 같은 모양으로: 호환 문자 정리 + 소문자 + 식재료 별칭."""
    text = unicodedata.normalize("NFKC", text).lower()
    for alias, name in FOOD_ALIASES.items():
        text = text.replace(alias, name)
    return text


def words(text: str) -> list[str]:
    return _WORD.findall(normalize(text))


def pack(vector: Vector) -> bytes:
    return array("f", vector).tobytes()


def unpack(data: bytes) -> Vector:
    vector = array("f")
    vector.frombytes(bytes(data))
    return vector


def cosine(a: Vector, b: Vector) -> float:
    """두 벡터가 모두 길이 1이면 내적이 곧 코사인 유사도다. 길이가 다르면(다른 방식으로 만든 벡터) 0."""
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


class LocalNgramEmbedder:
    DIM = 512
    WORD_WEIGHT = 1.5  # 단어 통째로 맞는 것을 글자 묶음보다 조금 더 친다
    _ALIASES = hashlib.sha1(repr(sorted(FOOD_ALIASES.items())).encode("utf-8")).hexdigest()[:6]

    name = f"local-ngram-v1:{DIM}:{_ALIASES}"
    min_similarity = 0.2

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> Vector:
        return self._vector(text)

    def _vector(self, text: str) -> Vector:
        features: Counter[str] = Counter()
        for word in words(text):
            features["w:" + word] += 1
            padded = f"^{word}$"  # 단어의 처음·끝 글자도 구별한다 (달걀을 ≈ 달걀말이)
            for size in (2, 3):
                for start in range(len(padded) - size + 1):
                    features[f"{size}:{padded[start:start + size]}"] += 1
        buckets = [0.0] * self.DIM
        for feature, count in features.items():
            digest = zlib.crc32(feature.encode("utf-8"))  # 실행마다 같은 값 (파이썬 hash()는 아니다)
            weight = (1 + math.log(count)) * (self.WORD_WEIGHT if feature.startswith("w:") else 1.0)
            buckets[digest % self.DIM] += weight if (digest >> 16) & 1 else -weight  # 부호 해시: 충돌이 서로 상쇄된다
        norm = math.sqrt(sum(value * value for value in buckets))
        if norm:
            buckets = [value / norm for value in buckets]
        return array("f", buckets)


_LOCAL = LocalNgramEmbedder()


def get_embedder() -> Embedder | None:
    """설정한 제공자. `none`이면 의미 점수 없이 키워드 점수만으로 검색한다 (문제가 생겼을 때 환경 변수만 바꿔 끌 수 있다)."""
    if settings.embedding_provider == "local":
        return _LOCAL
    return None
