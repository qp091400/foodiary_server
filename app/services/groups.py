"""Fridge groups: which fridges a person's groups contain."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Fridge, FridgeGroup, FridgeGroupItem


def my_group(db: Session, user_id: int, group_id: int) -> FridgeGroup | None:
    """The group if it is mine, else None (other people's groups are not revealed)."""
    group = db.get(FridgeGroup, group_id)
    return group if group is not None and group.owner_id == user_id else None


def groups_by_fridge(db: Session, user_id: int, fridge_ids: list[int]) -> dict[int, list[int]]:
    """For each fridge, the ids of my groups that contain it."""
    if not fridge_ids:
        return {}
    rows = db.execute(
        select(FridgeGroupItem.fridge_id, FridgeGroupItem.group_id)
        .join(FridgeGroup, FridgeGroup.id == FridgeGroupItem.group_id)
        .where(FridgeGroupItem.fridge_id.in_(fridge_ids), FridgeGroup.owner_id == user_id)
        .order_by(FridgeGroupItem.group_id)
    ).all()
    result: dict[int, list[int]] = {}
    for fridge_id, group_id in rows:
        result.setdefault(fridge_id, []).append(group_id)
    return result


def group_fridges(db: Session, group: FridgeGroup) -> list[Fridge]:
    """Fridges in the group, by id. Does not check whether the viewer can still see them."""
    return list(
        db.scalars(
            select(Fridge).join(FridgeGroupItem, FridgeGroupItem.fridge_id == Fridge.id).where(FridgeGroupItem.group_id == group.id).order_by(Fridge.id)
        ).all()
    )
