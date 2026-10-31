"""냉장고에서 내 권한을 판단한다.

냉장고에서 내 권한은 넷 중 하나다.
- owner: 주인
- editor: 공유 링크로 들어온 멤버 (FridgeMember, role=member). 주인처럼 고칠 수 있다
- viewer: 멤버는 아니지만 주인이 '모두에게'(public) 공개한 냉장고를 읽기만 하는 사람
- None: 아무것도 못 한다
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Fridge, FridgeMember


def member_permission(db: Session, user_id: int, fridge_id: int) -> str | None:
    """멤버로서의 권한: owner / editor / None. 공개 범위로 보이는 것은 세지 않는다."""
    role = db.scalar(select(FridgeMember.role).where(FridgeMember.fridge_id == fridge_id, FridgeMember.user_id == user_id))
    if role is None:
        return None
    return "owner" if role == "owner" else "editor"


def fridge_permission(db: Session, user_id: int, fridge: Fridge) -> str | None:
    """이 냉장고에서 내 권한. 멤버가 아니면 주인이 '모두에게' 공개했을 때만 보기 권한이 생긴다."""
    granted = member_permission(db, user_id, fridge.id)
    if granted is not None:
        return granted
    return "viewer" if fridge.visibility == "public" else None
