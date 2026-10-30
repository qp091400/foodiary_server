"""냉장고마다 친구 한 명의 권한을 정한다.

권한이 정해지는 순서 (위가 이긴다):
1. 없음(FridgeAccessBlock): 주인이 이 냉장고에 한해 '없음'으로 정해 둔 것. 묶음도 링크도 이 냉장고에는 못 준다.
2. 주인이 이 냉장고에 따로 준 권한 (derived=False 행): viewer(보기) 또는 editor(고치기)
3. 묶음 공유로 따라온 권한 (derived=True 행): 묶음이 바뀔 때마다 recompute_group_grants가 다시 만든다.

주인이 아닌 사람의 권한은 모두 FridgeMember(role=member, 고치기) 또는 FridgeViewer(보기) 행으로 저장하므로,
권한을 읽는 쪽(explicit_permission 등)은 따로가 묶음에서 왔는지 신경 쓰지 않아도 된다.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Fridge, FridgeAccessBlock, FridgeGroup, FridgeGroupItem, FridgeGroupShare, FridgeMember, FridgeViewer
from app.services.friends import are_friends

RANK = {None: 0, "viewer": 1, "editor": 2}


def is_blocked(db: Session, fridge_id: int, user_id: int) -> bool:
    return db.get(FridgeAccessBlock, (fridge_id, user_id)) is not None


def _rows(db: Session, fridge_id: int, user_id: int) -> tuple[FridgeMember | None, FridgeViewer | None, FridgeAccessBlock | None]:
    member = db.scalar(
        select(FridgeMember).where(FridgeMember.fridge_id == fridge_id, FridgeMember.user_id == user_id, FridgeMember.role == "member")
    )
    return member, db.get(FridgeViewer, (fridge_id, user_id)), db.get(FridgeAccessBlock, (fridge_id, user_id))


def set_permission(db: Session, fridge: Fridge, user_id: int, permission: str) -> None:
    """이 냉장고에 한해 이 사람의 권한을 viewer / editor / none으로 정한다. 묶음에서 따라온 것보다 우선한다. (commit은 부른 쪽에서)"""
    member, viewer, block = _rows(db, fridge.id, user_id)
    if permission == "none":
        if member:
            db.delete(member)
        if viewer:
            db.delete(viewer)
        if block is None:
            db.add(FridgeAccessBlock(fridge_id=fridge.id, user_id=user_id))
        return
    if block:
        db.delete(block)
    if permission == "editor":
        if viewer:
            db.delete(viewer)
        if member:
            member.derived = False
        else:
            db.add(FridgeMember(fridge_id=fridge.id, user_id=user_id, role="member"))
    else:
        if member:
            db.delete(member)
        if viewer:
            viewer.derived = False
        else:
            db.add(FridgeViewer(fridge_id=fridge.id, user_id=user_id))


def reset_permission(db: Session, fridge: Fridge, user_id: int) -> bool:
    """이 냉장고에 따로 정한 것(권한, 없음)을 지우고 묶음에서 따라오는 권한에 맡긴다. 지운 것이 있으면 True. (commit은 부른 쪽에서)"""
    member, viewer, block = _rows(db, fridge.id, user_id)
    removed = False
    if member and not member.derived:
        db.delete(member)
        removed = True
    if viewer and not viewer.derived:
        db.delete(viewer)
        removed = True
    if block:
        db.delete(block)
        removed = True
    if removed:
        db.flush()
        recompute_group_grants(db, fridge.owner_id, user_id)
    return removed


def recompute_group_grants(db: Session, owner_id: int, user_id: int) -> None:
    """owner가 user에게 공유한 묶음들에서 따라오는 권한을 처음부터 다시 만든다. (commit은 부른 쪽에서)

    - 묶음 주인이 주인인 냉장고에만 따라간다 (남의 냉장고는 내가 권한을 줄 수 없다).
    - 한 냉장고가 여러 공유 묶음에 있으면 가장 높은 권한.
    - 주인이 그 냉장고에 따로 정한 권한이나 '없음'이 있으면 건드리지 않는다.
    - 둘이 친구가 아니면 아무것도 따라가지 않는다.
    """
    wanted: dict[int, str] = {}
    shares = {}
    if are_friends(db, owner_id, user_id):
        shares = dict(
            db.execute(
                select(FridgeGroupShare.group_id, FridgeGroupShare.permission)
                .join(FridgeGroup, FridgeGroup.id == FridgeGroupShare.group_id)
                .where(FridgeGroup.owner_id == owner_id, FridgeGroupShare.user_id == user_id)
            ).all()
        )
    if shares:
        for group_id, fridge_id in db.execute(
            select(FridgeGroupItem.group_id, Fridge.id)
            .join(Fridge, Fridge.id == FridgeGroupItem.fridge_id)
            .where(FridgeGroupItem.group_id.in_(list(shares)), Fridge.owner_id == owner_id)
        ).all():
            if RANK[shares[group_id]] > RANK[wanted.get(fridge_id)]:
                wanted[fridge_id] = shares[group_id]

    owned = select(Fridge.id).where(Fridge.owner_id == owner_id)
    members = db.scalars(
        select(FridgeMember).where(FridgeMember.user_id == user_id, FridgeMember.role == "member", FridgeMember.fridge_id.in_(owned))
    ).all()
    viewers = db.scalars(select(FridgeViewer).where(FridgeViewer.user_id == user_id, FridgeViewer.fridge_id.in_(owned))).all()
    blocked = set(db.scalars(select(FridgeAccessBlock.fridge_id).where(FridgeAccessBlock.user_id == user_id, FridgeAccessBlock.fridge_id.in_(owned))).all())
    explicit = {row.fridge_id for row in (*members, *viewers) if not row.derived}
    target = {fridge_id: permission for fridge_id, permission in wanted.items() if fridge_id not in explicit and fridge_id not in blocked}

    kept_editors, kept_viewers = set(), set()
    for member in members:
        if member.derived and target.get(member.fridge_id) != "editor":
            db.delete(member)
        else:
            kept_editors.add(member.fridge_id)
    for viewer in viewers:
        if viewer.derived and target.get(viewer.fridge_id) != "viewer":
            db.delete(viewer)
        else:
            kept_viewers.add(viewer.fridge_id)
    for fridge_id, permission in target.items():
        if permission == "editor" and fridge_id not in kept_editors:
            db.add(FridgeMember(fridge_id=fridge_id, user_id=user_id, role="member", derived=True))
        elif permission == "viewer" and fridge_id not in kept_viewers:
            db.add(FridgeViewer(fridge_id=fridge_id, user_id=user_id, derived=True))
    db.flush()


def recompute_for_group(db: Session, group: FridgeGroup) -> None:
    """묶음의 구성이나 공유가 바뀌었을 때, 그 묶음을 공유받은 모든 사람의 따라오는 권한을 다시 만든다."""
    for user_id in db.scalars(select(FridgeGroupShare.user_id).where(FridgeGroupShare.group_id == group.id)).all():
        recompute_group_grants(db, group.owner_id, user_id)
