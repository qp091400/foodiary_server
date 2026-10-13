"""DB 모델. 시각은 모두 naive UTC, 날짜(purchased_on 등)는 Asia/Seoul 기준 달력 날짜."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.timeutil import utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(20), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    nickname: Mapped[str] = mapped_column(String(20))
    # 비밀번호 변경·재설정 때 +1 → 이전에 발급한 토큰 무효화
    token_version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class EmailVerification(Base):
    __tablename__ = "email_verifications"
    __table_args__ = (Index("ix_email_verifications_email_purpose", "email", "purpose"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(20))  # signup / find_id / reset_password
    code_hash: Mapped[str] = mapped_column(String(64))  # HMAC-SHA256, 평문 저장 금지
    expires_at: Mapped[datetime]
    attempts: Mapped[int] = mapped_column(default=0)
    verified_at: Mapped[datetime | None]
    consumed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Fridge(Base):
    __tablename__ = "fridges"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class FridgeMember(Base):
    __tablename__ = "fridge_members"
    __table_args__ = (UniqueConstraint("fridge_id", "user_id", name="uq_fridge_members_fridge_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fridge_id: Mapped[int] = mapped_column(ForeignKey("fridges.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # owner / member
    joined_at: Mapped[datetime] = mapped_column(default=utcnow)


class FridgeInvite(Base):
    __tablename__ = "fridge_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    fridge_id: Mapped[int] = mapped_column(ForeignKey("fridges.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(8), unique=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime]


class Receipt(Base):
    __tablename__ = "receipts"
    # receipt_barcode가 NULL인 행은 여러 개 허용된다 (PostgreSQL·SQLite 모두).
    __table_args__ = (UniqueConstraint("fridge_id", "receipt_barcode", name="uq_receipts_fridge_barcode"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fridge_id: Mapped[int] = mapped_column(ForeignKey("fridges.id", ondelete="CASCADE"), index=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending / confirmed
    store_name: Mapped[str | None] = mapped_column(String(100))
    purchased_on: Mapped[date | None]
    total_amount: Mapped[int | None]
    receipt_barcode: Mapped[str | None] = mapped_column(String(64))
    image_path: Mapped[str | None] = mapped_column(String(255))  # UPLOAD_DIR 기준 상대 경로
    raw_lines: Mapped[list[str]] = mapped_column(JSON, default=list)
    draft_items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    confirmed_at: Mapped[datetime | None]


class FoodItem(Base):
    __tablename__ = "food_items"
    __table_args__ = (
        Index("ix_food_items_fridge_status_expiry", "fridge_id", "status", "expiry_date"),
        Index("ix_food_items_fridge_purchased", "fridge_id", "purchased_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fridge_id: Mapped[int] = mapped_column(ForeignKey("fridges.id", ondelete="CASCADE"))
    receipt_id: Mapped[int | None] = mapped_column(ForeignKey("receipts.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(30))
    storage: Mapped[str] = mapped_column(String(10))  # fridge / freezer / room
    barcode: Mapped[str | None] = mapped_column(String(14))
    quantity: Mapped[float]
    remaining_quantity: Mapped[float]
    unit_price: Mapped[int | None]
    total_price: Mapped[int | None]  # 할인 반영 실결제액
    purchased_on: Mapped[date]
    expiry_date: Mapped[date | None]
    expiry_source: Mapped[str | None] = mapped_column(String(10))  # barcode / category / manual
    status: Mapped[str] = mapped_column(String(10), default="active")  # active / consumed / discarded
    memo: Mapped[str | None] = mapped_column(Text)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None]


class ReceiptItem(Base):
    """확정된 영수증의 모든 줄 (비식품 포함 = 구매 기록)."""

    __tablename__ = "receipt_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int]
    name: Mapped[str] = mapped_column(String(200))
    barcode: Mapped[str | None] = mapped_column(String(14))
    quantity: Mapped[float]
    unit_price: Mapped[int | None]
    amount: Mapped[int]
    discount: Mapped[int] = mapped_column(default=0)
    category: Mapped[str | None] = mapped_column(String(30))
    included: Mapped[bool]
    food_item_id: Mapped[int | None] = mapped_column(ForeignKey("food_items.id", ondelete="SET NULL"))


class ConsumptionLog(Base):
    """먹음/버림 기록. 식품을 지워도 통계가 남도록 이름·카테고리를 복사해 둔다."""

    __tablename__ = "consumption_logs"
    __table_args__ = (Index("ix_consumption_logs_fridge_logged", "fridge_id", "logged_on"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fridge_id: Mapped[int] = mapped_column(ForeignKey("fridges.id", ondelete="CASCADE"))
    food_item_id: Mapped[int | None] = mapped_column(ForeignKey("food_items.id", ondelete="SET NULL"))
    item_name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(30))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(10))  # consume / discard
    quantity: Mapped[float]
    value: Mapped[int]  # 원
    logged_on: Mapped[date]  # Asia/Seoul 기준 날짜
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ProductCache(Base):
    """식품안전나라 C005 조회 결과 캐시 ("없음"도 캐시한다)."""

    __tablename__ = "product_cache"

    barcode: Mapped[str] = mapped_column(String(14), primary_key=True)
    found: Mapped[bool]
    name: Mapped[str | None] = mapped_column(String(200))
    food_type: Mapped[str | None] = mapped_column(String(100))
    manufacturer: Mapped[str | None] = mapped_column(String(200))
    shelf_life_text: Mapped[str | None] = mapped_column(String(300))
    shelf_life_days: Mapped[int | None]
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)
