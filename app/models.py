"""DB 모델. 시각은 모두 naive UTC, 날짜(purchased_on 등)는 Asia/Seoul 기준 달력 날짜."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, LargeBinary, String, Text, UniqueConstraint, select, text
from sqlalchemy.orm import Mapped, mapped_column, object_session

from app.database import Base
from app.timeutil import utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(20), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    nickname: Mapped[str] = mapped_column(String(20))
    household_size: Mapped[int | None]
    # 비밀번호 변경·재설정 때 +1 → 이전에 발급한 토큰 무효화
    token_version: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # 관리자: 남의 글도 지울 수 있다. scripts/set_admin.py로만 바꾼다
    is_admin: Mapped[bool] = mapped_column(default=False)
    avatar_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 알림 아이콘: 내 글에 달린 댓글 중 이 번호까지는 봤다 (댓글 번호는 쓴 순서대로 커진다)
    comments_seen_id: Mapped[int] = mapped_column(default=0)

    @property
    def avatar_url(self) -> str | None:
        return f"/users/{self.id}/avatar/{self.avatar_path.rsplit('/', 1)[-1]}" if self.avatar_path else None

    @property
    def community_suspension(self) -> "CommunitySuspension | None":
        """지금 걸려 있는 커뮤니티 임시 정지. 없으면 None. (내 정보 응답에 실려서, 정지된 사람이 앱에서 자기 상태를 본다)"""
        session = object_session(self)
        if session is None:
            return None
        return session.scalar(
            select(CommunitySuspension)
            .where(CommunitySuspension.user_id == self.id, CommunitySuspension.lifted_at.is_(None), CommunitySuspension.ends_at > utcnow())
            .order_by(CommunitySuspension.ends_at.desc())
            .limit(1)
        )


class CommunitySuspension(Base):
    """관리자가 건 커뮤니티 임시 정지. 기간 동안 글·댓글·후기를 쓸 수 없다. 풀거나 끝나도 기록은 남긴다.

    지금 정지 중인 것 = lifted_at이 비어 있고 ends_at이 아직 안 지난 것.
    """

    __tablename__ = "community_suspensions"
    __table_args__ = (Index("ix_community_suspensions_user_ends", "user_id", "ends_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    ends_at: Mapped[datetime]
    lifted_at: Mapped[datetime | None]
    lifted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class PushDevice(Base):
    __tablename__ = "push_devices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_version: Mapped[int]
    platform: Mapped[str] = mapped_column(String(10))
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    credential: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(default=True)
    expiry: Mapped[bool] = mapped_column(default=True)
    comments: Mapped[bool] = mapped_column(default=True)
    comments_after_id: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class PushDelivery(Base):
    __tablename__ = "push_deliveries"
    __table_args__ = (UniqueConstraint("device_id", "event_key", name="uq_push_delivery_event"),
                     Index("ix_push_delivery_pending", "status", "next_attempt_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("push_devices.id", ondelete="CASCADE"))
    event_key: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(10))
    target_id: Mapped[int]
    status: Mapped[str] = mapped_column(String(12), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(default=utcnow)
    ticket: Mapped[str | None] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(50))
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
    # 멤버가 아닌 사람에게 품목을 보여 줄 범위: private(나만) / public(로그인한 모두가 읽기만)
    visibility: Mapped[str] = mapped_column(String(10), default="private")
    # 공유 링크의 토큰. 처음 공유할 때 만들어지고, 주인이 바꾸면 이전 링크와 QR로는 들어올 수 없다
    share_token: Mapped[str | None] = mapped_column(String(32), unique=True)


class FridgeMember(Base):
    __tablename__ = "fridge_members"
    __table_args__ = (UniqueConstraint("fridge_id", "user_id", name="uq_fridge_members_fridge_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fridge_id: Mapped[int] = mapped_column(ForeignKey("fridges.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # owner / member
    joined_at: Mapped[datetime] = mapped_column(default=utcnow)


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
    unit: Mapped[str] = mapped_column(String(4), default="개", server_default="개")  # 수량 단위: 개 / g / kg / ml / L
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
    unit: Mapped[str] = mapped_column(String(4), default="개", server_default="개")
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


class Post(Base):
    """커뮤니티 글. 지운 글은 deleted_at을 채워 두고 보여 주지 않는다 (누가 지웠는지 남긴다)."""

    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_deleted_at_id", "deleted_at", "id"),
        Index("ix_posts_board_deleted_id", "board", "deleted_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text)
    board: Mapped[str] = mapped_column(String(10), default="general", server_default="general")
    review_id: Mapped[int | None] = mapped_column(ForeignKey("recipe_reviews.id", ondelete="SET NULL"), unique=True)
    # 글에 나온 주소마다의 미리보기 목록(app/services/link_preview.py). 글을 쓰거나 고칠 때 한 번 만들어 저장한다.
    # None은 이 칸이 생기기 전에 쓴 글(link_preview만 있다), []는 만들어 봤는데 없다는 뜻이다
    link_previews: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 첫 번째 미리보기. 목록을 모르는 옛 서버·옛 앱을 위해 계속 같이 저장한다 (0014에서 만든 칸)
    link_preview: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None]
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class PostEmbedding(Base):
    """글 검색용 벡터(임베딩). 검색할 때 없거나 낡은 것만 다시 만든다 (app/services/post_search.py).

    글이 바뀌었는지는 content_hash로, 만든 방식이 바뀌었는지는 model로 알아본다. 그래서 글을 쓰고 고치고 지우는 코드는 이 표를 몰라도 된다.
    """

    __tablename__ = "post_embeddings"

    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[str] = mapped_column(String(60))  # 벡터를 만든 방식 (Embedder.name)
    content_hash: Mapped[str] = mapped_column(String(40))  # 벡터를 만들 때 쓴 글 내용의 SHA-1
    vector: Mapped[bytes] = mapped_column(LargeBinary)  # float32 배열
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


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
