"""사용자가 영수증 초안의 이름을 고친 기록으로 다음 영수증의 이름을 고친다. ("코카율라" → "코카콜라")

- 확정할 때 "OCR이 읽은 이름"과 "사용자가 확정한 이름"을 견주어, OCR 오타로 보이는 차이만 기록한다(learn).
  낱말 수가 같으면 달라진 낱말만, 달라졌으면 이름 전체를 기록한다. 이름을 통째로 바꾼 것(삼겹살 구이용 → 돼지고기)은 기록하지 않는다.
- 다음 영수증에서는 그 이름이 나오면 고친 이름으로 바꾼다(load → fix).
  고친 본인에게는 바로 적용하고, 다른 사람에게는 서로 다른 사용자가 NAME_LEARNING_MIN_USERS명 이상 같게 고쳤을 때만 적용한다.
  한 사람의 장난이나 실수가 다른 사람의 영수증에 퍼지지 않게 하려는 것이다. 같은 이름에 다른 이름이 같은 힘으로 맞서면 고르지 않는다.
- 사용자가 보정된 이름을 다시 원래 읽은 이름으로 돌려 놓으면 그 사람의 기록을 지운다(보정이 틀렸다는 뜻이다).
- 기록은 영수증 확정이 끝난 뒤에 따로 저장한다. 기록하다 실패해도 확정은 그대로 성공한다.
"""

import difflib
import logging
import re
import unicodedata
from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import NameCorrection

logger = logging.getLogger(__name__)

MAX_LEN = 100
SIMILAR = 0.6  # 읽은 이름과 고친 이름이 이만큼 닮아야 OCR 오타로 본다 (difflib 비율)
_HANGUL = re.compile(r"[가-힣]")


def normalize(name: str) -> str:
    return unicodedata.normalize("NFC", name).strip()


def _typo_like(seen: str, fixed: str) -> bool:
    return (
        2 <= len(seen) <= MAX_LEN
        and 2 <= len(fixed) <= MAX_LEN
        and bool(_HANGUL.search(seen))
        and bool(_HANGUL.search(fixed))
        and difflib.SequenceMatcher(None, seen, fixed).ratio() >= SIMILAR
    )


def correction_pairs(read: str, final: str) -> list[tuple[str, str]]:
    """읽은 이름과 확정한 이름의 차이 중 OCR 오타로 보이는 것. 낱말 수가 같으면 달라진 낱말마다, 아니면 이름 전체 하나."""
    before, after = normalize(read).split(), normalize(final).split()
    if before == after:
        return []
    pairs = [(x, y) for x, y in zip(before, after) if x != y] if len(before) == len(after) else [(" ".join(before), " ".join(after))]
    return [pair for pair in pairs if _typo_like(*pair)]


def rejected_words(read: str, shown: str, final: str) -> list[str]:
    """보정이 바꿔 놓은 낱말을 사용자가 원래 읽은 낱말로 되돌렸다면 그 낱말들. (낱말 수가 같을 때만 알 수 있다)"""
    a, b, c = (normalize(text).split() for text in (read, shown, final))
    if not (len(a) == len(b) == len(c)):
        return []
    return [x for x, y, z in zip(a, b, c) if x != y and z == x]


class LearnedNames:
    def __init__(self, fixes: dict[str, str] | None = None) -> None:
        self._fixes = fixes or {}

    def fix(self, name: str) -> str | None:
        """고친 이름. 고칠 게 없으면 None."""
        if not self._fixes:
            return None
        name = normalize(name)
        if name in self._fixes:
            return self._fixes[name]
        words = name.split()
        fixed = [self._fixes.get(word, word) for word in words]
        return " ".join(fixed) if fixed != words else None


def load(db: Session, user_id: int | None, names: Iterable[str]) -> LearnedNames:
    """names에 나온 이름·낱말 중 고쳐 둔 것을 모은다. user_id가 없으면(개발용 페이지) 여러 사람이 같게 고친 것만 쓴다."""
    if not settings.name_learning_enabled:
        return LearnedNames()
    keys: set[str] = set()
    for name in names:
        text = normalize(name)
        keys.add(text)
        keys.update(text.split())
    keys = {key for key in keys if 2 <= len(key) <= MAX_LEN}
    if not keys:
        return LearnedNames()
    votes: dict[str, dict[str, set[int]]] = {}
    for seen, fixed, voter in db.execute(
        select(NameCorrection.seen, NameCorrection.fixed, NameCorrection.user_id).where(NameCorrection.seen.in_(sorted(keys)))
    ):
        votes.setdefault(seen, {}).setdefault(fixed, set()).add(voter)
    fixes: dict[str, str] = {}
    for seen, options in votes.items():
        ranked = sorted(
            ((user_id in voters, len(voters), fixed) for fixed, voters in options.items() if user_id in voters or len(voters) >= settings.name_learning_min_users),
            reverse=True,
        )
        if not ranked or (len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]):
            continue  # 믿을 만한 고침이 없거나, 같은 힘의 다른 고침이 맞선다
        fixes[seen] = ranked[0][2]
    return LearnedNames(fixes)


def learn(db: Session, user_id: int, edits: Iterable[tuple[str, str, str]]) -> None:
    """edits는 (OCR이 읽은 이름, 초안에 보여 준 이름, 사용자가 확정한 이름). 영수증 확정을 저장한 뒤에 부른다. 실패해도 예외를 던지지 않는다."""
    if not settings.name_learning_enabled:
        return
    try:
        adds: dict[str, str] = {}
        rejected: set[str] = set()
        for read, shown, final in edits:
            rejected.update(rejected_words(read, shown, final))
            for seen, fixed in correction_pairs(read, final):
                adds[seen] = fixed
        if not adds and not rejected:
            return
        if rejected:
            db.execute(delete(NameCorrection).where(NameCorrection.user_id == user_id, NameCorrection.seen.in_(sorted(rejected))))
        mine = {row.seen: row for row in db.scalars(select(NameCorrection).where(NameCorrection.user_id == user_id, NameCorrection.seen.in_(list(adds))))} if adds else {}
        for seen, fixed in adds.items():
            if seen in mine:
                mine[seen].fixed = fixed  # 다시 고쳤다: 마지막 고침이 이긴다
            else:
                db.add(NameCorrection(user_id=user_id, seen=seen, fixed=fixed))
        db.commit()
    except Exception:  # noqa: BLE001 - 이름을 배우지 못해도 영수증 확정은 이미 끝났다 (동시에 같은 이름을 기록한 경우 포함)
        db.rollback()
        logger.warning("상품 이름 고침을 기록하지 못함", exc_info=True)
