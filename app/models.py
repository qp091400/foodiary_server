"""DB 모델. 시각은 모두 naive UTC, 날짜(purchased_on 등)는 Asia/Seoul 기준 달력 날짜."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.timeutil import utcnow


def new_friend_code() -> str:
    from app.security import generate_invite_code  # security가 이 파일을 import해서, 순환을 피하려고 부를 때 가져온다

    return generate_invite_code()


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
    # 친구 추가용 코드(링크에 담아 공유한다). 새로 뽑을 수 있다
    friend_code: Mapped[str] = mapped_column(String(8), unique=True, default=new_friend_code)
    # 관리자: 남의 글도 지울 수 있다. scripts/set_admin.py로만 바꾼다
    is_admin: Mapped[bool] = mapped_column(default=False)
    avatar_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 알림 아이콘: 내 글에 달린 댓글 중 이 번호까지는 봤다 (댓글 번호는 쓴 순서대로 커진다)
    comments_seen_id: Mapped[int] = mapped_column(default=0)

    @property
    def avatar_url(self) -> str | None:
        return f"/users/{self.id}/avatar/{self.avatar_path.rsplit('/', 1)[-1]}" if self.avatar_path else None


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
    # 멤버가 아닌 사람에게 품목을 보여 줄 범위: private(나만) / friends(친구들도) / public(모두)
    visibility: Mapped[str] = mapped_column(String(10), default="private")


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


class Friendship(Base):
    """친구 관계. 공유 링크로 들어오면 바로 accepted로 만들어진다. (예전 요청 방식의 pending은 이제 쓰지 않는다)"""

    __tablename__ = "friendships"
    __table_args__ = (UniqueConstraint("requester_id", "addressee_id", name="uq_friendships_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    addressee_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending / accepted
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    responded_at: Mapped[datetime | None]


class Post(Base):
    """커뮤니티 글. 지운 글은 deleted_at을 채워 두고 보여 주지 않는다 (누가 지웠는지 남긴다)."""

    __tablename__ = "posts"
    __table_args__ = (Index("ix_posts_deleted_at_id", "deleted_at", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None]
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class PostLike(Base):
    """글 좋아요. 한 사람이 한 글에 하나."""

    __tablename__ = "post_likes"

    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Comment(Base):
    """글의 댓글. 글처럼 지운 댓글은 deleted_at을 채워 두고 보여 주지 않는다."""

    __tablename__ = "comments"
    __table_args__ = (Index("ix_comments_post_id_deleted_at_id", "post_id", "deleted_at", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None]
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class RecipeReview(Base):
    """레시피 별점과 후기. 한 사람이 한 레시피에 하나만 쓰고(지운 것은 빼고), 지운 후기는 글처럼 deleted_at을 채워 두고 보여 주지 않는다."""

    __tablename__ = "recipe_reviews"
    __table_args__ = (
        Index("ix_recipe_reviews_recipe_deleted_id", "recipe_id", "deleted_at", "id"),
        Index(
            "uq_recipe_reviews_live_author",
            "recipe_id",
            "author_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[str] = mapped_column(String(64))  # 레시피 자료의 번호. 레시피는 DB 표가 아니라 자료 파일이라 외래 키는 걸지 않는다
    recipe_name: Mapped[str] = mapped_column(String(200))  # 쓸 때의 이름. 자료를 다시 가져와 레시피가 없어져도 무엇이었는지 남는다
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int]  # 1~5
    body: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime | None]  # 고친 적이 없으면 비어 있다
    deleted_at: Mapped[datetime | None]
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class PostPhoto(Base):
    """글에 붙인 사진. 파일은 uploads/posts/{글 번호}/ 아래에 둔다."""

    __tablename__ = "post_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(120))  # uploads 폴더 기준 상대 경로
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Report(Base):
    """글, 댓글, 레시피 후기 신고. 한 사람이 같은 대상을 한 번만 신고할 수 있고, 관리자가 지우거나 문제없음으로 처리한다."""

    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("reporter_id", "target_type", "target_id", name="uq_reports_reporter_target"),
        Index("ix_reports_status_id", "status", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    target_type: Mapped[str] = mapped_column(String(10))  # post / comment / review
    target_id: Mapped[int]  # 글, 댓글, 후기 번호 (대상이 여럿이라 외래 키는 걸지 않는다)
    reason: Mapped[str] = mapped_column(String(20))  # spam / abuse / inappropriate / privacy / other
    detail: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(10), default="open")  # open / resolved(지움) / dismissed(문제없음)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    handled_at: Mapped[datetime | None]


class FridgeViewer(Base):
    """냉장고를 볼 수만 있는 친구. 변경 요청은 보낼 수 있다. 고칠 수 있는 친구는 FridgeMember(role=member)다."""

    __tablename__ = "fridge_viewers"

    fridge_id: Mapped[int] = mapped_column(ForeignKey("fridges.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class FridgeShareLink(Base):
    """냉장고 공유 링크. 링크로 들어온 사람은 주인과 친구가 되고, permission만큼의 권한을 받는다."""

    __tablename__ = "fridge_share_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    fridge_id: Mapped[int] = mapped_column(ForeignKey("fridges.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(32), unique=True)
    permission: Mapped[str] = mapped_column(String(10))  # viewer(보기, 변경 요청) / editor(주인처럼 고치기)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    max_uses: Mapped[int | None]  # 비우면 횟수 제한 없음
    use_count: Mapped[int] = mapped_column(default=0)
    revoked_at: Mapped[datetime | None]


class ChangeRequest(Base):
    """친구가 냉장고 주인에게 보내는 변경 요청. 주인이 승인하면 그때 냉장고에 반영된다."""

    __tablename__ = "change_requests"
    __table_args__ = (Index("ix_change_requests_fridge_id_status", "fridge_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fridge_id: Mapped[int] = mapped_column(ForeignKey("fridges.id", ondelete="CASCADE"))
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # add(추가) / consume(먹음) / discard(버림)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("food_items.id", ondelete="SET NULL"))
    item_name: Mapped[str | None] = mapped_column(String(200))  # 요청할 때의 이름. 품목이 나중에 없어져도 무엇이었는지 남는다
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # quantity, name, category, storage, expiry_date
    note: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending / approved / declined / cancelled
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    handled_at: Mapped[datetime | None]
    # 요청한 사람이 승인·거절된 결과를 알림에서 확인한 시각. 비어 있으면 아직 못 본 결과다
    result_seen_at: Mapped[datetime | None]

