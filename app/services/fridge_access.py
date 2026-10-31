"""냉장고별 참여 권한과 전체 공개 여부만으로 접근을 판단한다."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Fridge, FridgeMember, FridgeViewer


def explicit_permission(db: Session, user_id: int, fridge_id: int) -> str | None:
    role = db.scalar(select(FridgeMember.role).where(FridgeMember.fridge_id == fridge_id, FridgeMember.user_id == user_id))
    if role is not None:
        return "owner" if role == "owner" else "editor"
    if db.scalar(select(FridgeViewer.user_id).where(FridgeViewer.fridge_id == fridge_id, FridgeViewer.user_id == user_id)):
        return "viewer"
    return None


def fridge_permission(db: Session, viewer_id: int, fridge: Fridge) -> str | None:
    granted = explicit_permission(db, viewer_id, fridge.id)
    return granted if granted is not None else "viewer" if fridge.visibility == "public" else None


def can_request_changes(db: Session, viewer_id: int, fridge: Fridge, permission: str | None) -> bool:
    # Legacy request endpoints remain readable, but new changes require editor permission.
    return False
