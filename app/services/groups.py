"""냉장고 묶음: 누가 어떤 묶음을 볼 수 있는지, 묶음 안에 어떤 냉장고가 있는지."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Fridge, FridgeGroup, FridgeGroupItem, FridgeGroupShare


def visible_group(db: Session, user_id: int, group_id: int) -> FridgeGroup | None:
    """내 묶음이거나 친구가 나에게 공유한 묶음이면 그 묶음, 아니면 None. (있는지도 알려 주지 않으려고 둘을 구별하지 않는다)"""
    group = db.get(FridgeGroup, group_id)
    if group is None:
        return None
    if group.owner_id == user_id:
        return group
    shared = db.scalar(select(FridgeGroupShare.id).where(FridgeGroupShare.group_id == group_id, FridgeGroupShare.user_id == user_id))
    return group if shared is not None else None


def groups_by_fridge(db: Session, user_id: int, fridge_ids: list[int]) -> dict[int, list[int]]:
    """냉장고마다, 그 냉장고가 들어 있는 묶음 중 내가 볼 수 있는 것(내 묶음, 나에게 공유된 묶음)의 번호."""
    if not fridge_ids:
        return {}
    shared = select(FridgeGroupShare.group_id).where(FridgeGroupShare.user_id == user_id)
    rows = db.execute(
        select(FridgeGroupItem.fridge_id, FridgeGroupItem.group_id)
        .join(FridgeGroup, FridgeGroup.id == FridgeGroupItem.group_id)
        .where(FridgeGroupItem.fridge_id.in_(fridge_ids), or_(FridgeGroup.owner_id == user_id, FridgeGroup.id.in_(shared)))
        .order_by(FridgeGroupItem.group_id)
    ).all()
    result: dict[int, list[int]] = {}
    for fridge_id, group_id in rows:
        result.setdefault(fridge_id, []).append(group_id)
    return result


def group_fridges(db: Session, group: FridgeGroup) -> list[Fridge]:
    """묶음에 들어 있는 냉장고 (냉장고 번호 순). 보는 사람이 그 냉장고를 볼 수 있는지는 따지지 않는다."""
    return list(
        db.scalars(
            select(Fridge).join(FridgeGroupItem, FridgeGroupItem.fridge_id == Fridge.id).where(FridgeGroupItem.group_id == group.id).order_by(Fridge.id)
        ).all()
    )
