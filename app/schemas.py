"""요청·응답 스키마."""

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    computed_field,
    model_validator,
)

from app.services.expiry import CATEGORIES, label_of
from app.services.size import extract_size
from app.timeutil import today

# ── 공통 타입 ────────────────────────────────────────────────────────────

USERNAME_RE = re.compile(r"[a-z0-9_]{4,20}")
USERNAME_RULE = "아이디는 4~20자의 영문 소문자, 숫자, 밑줄(_)만 사용할 수 있습니다."
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
BARCODE_RE = re.compile(r"\d{8,14}")
BARCODE_RULE = "바코드는 8~14자리 숫자여야 합니다."


def _lower(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


def _username(value: str) -> str:
    if not USERNAME_RE.fullmatch(value):
        raise ValueError(USERNAME_RULE)
    return value


def _email(value: str) -> str:
    if len(value) > 255 or not EMAIL_RE.fullmatch(value):
        raise ValueError("이메일 형식이 올바르지 않습니다.")
    return value


def _password(value: str) -> str:
    if not 8 <= len(value) <= 64:
        raise ValueError("비밀번호는 8~64자여야 합니다.")
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("비밀번호는 영문과 숫자를 모두 포함해야 합니다.")
    if len(value.encode("utf-8")) > 72:  # bcrypt 입력 한계
        raise ValueError("비밀번호가 너무 깁니다. 한글·특수문자를 줄여 주세요.")
    return value


def _blank_to_none(value: object) -> object:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _barcode(value: str | None) -> str | None:
    if value is not None and not BARCODE_RE.fullmatch(value):
        raise ValueError(BARCODE_RULE)
    return value


def _category(value: str | None) -> str | None:
    if value is not None and value not in CATEGORIES:
        raise ValueError("알 수 없는 카테고리입니다. /categories 목록의 key를 사용해 주세요.")
    return value


def _utc_iso(value: datetime) -> str:
    return value.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


Username = Annotated[str, BeforeValidator(_lower), AfterValidator(_username)]
LooseUsername = Annotated[str, BeforeValidator(_lower)]  # 로그인 등: 형식이 틀려도 "일치하지 않음"으로 처리
Email = Annotated[str, BeforeValidator(_lower), AfterValidator(_email)]
Password = Annotated[str, AfterValidator(_password)]
Nickname = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
ItemName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Barcode = Annotated[str | None, BeforeValidator(_blank_to_none), AfterValidator(_barcode)]
CategoryKey = Annotated[str | None, BeforeValidator(_blank_to_none), AfterValidator(_category)]
Storage = Literal["fridge", "freezer", "room"]
Unit = Literal["개", "g", "kg", "ml", "L"]
Purpose = Literal["signup", "find_id", "reset_password"]
# DB의 naive UTC 시각을 "2026-10-13T05:21:09Z" 형태로 내보낸다.
UtcDatetime = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str, when_used="json")]


class MessageResponse(BaseModel):
    message: str


class SizeFields(BaseModel):
    """상품명(name)에서 뽑은 용량·규격. 이름에 표기가 없으면 모두 null이다."""

    @computed_field(description='정리한 용량 표기. 예: "1L", "200ml×6", "100매"')
    @property
    def size_text(self) -> str | None:
        size = extract_size(getattr(self, "name", None))
        return size.text if size else None

    @computed_field(description="총량. 부피는 ml, 무게는 g, 개수는 그 수 (묶음이면 곱한 값)")
    @property
    def size_amount(self) -> float | None:
        size = extract_size(getattr(self, "name", None))
        return size.amount if size else None

    @computed_field(description='size_amount의 단위. "ml", "g" 또는 세는 말("입", "매", "구" …)')
    @property
    def size_unit(self) -> str | None:
        size = extract_size(getattr(self, "name", None))
        return size.unit if size else None


# ── 인증·사용자 ──────────────────────────────────────────────────────────


class UsernameCheck(BaseModel):
    available: bool
    reason: str | None = None


class SendCodeRequest(BaseModel):
    email: Email
    purpose: Purpose
    username: LooseUsername | None = None

    @model_validator(mode="after")
    def _reset_needs_username(self) -> "SendCodeRequest":
        if self.purpose == "reset_password" and not self.username:
            raise ValueError("비밀번호 재설정에는 아이디(username)가 필요합니다.")
        return self


class VerifyCodeRequest(BaseModel):
    email: Email
    purpose: Purpose
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12)]


class VerifyCodeResponse(BaseModel):
    verification_token: str
    expires_in: int


class SignupRequest(BaseModel):
    username: Username
    email: Email
    password: Password
    nickname: Nickname
    verification_token: str


class LoginRequest(BaseModel):
    username: LooseUsername
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class FindIdRequest(BaseModel):
    verification_token: str


class FindIdResponse(BaseModel):
    username: str
    created_at: UtcDatetime


class ResetPasswordRequest(BaseModel):
    username: LooseUsername
    verification_token: str
    new_password: Password


class SuspensionInfo(BaseModel):
    """내가 지금 받고 있는 커뮤니티 임시 정지. 이 기간에는 글·댓글·후기를 쓸 수 없다(읽기, 좋아요, 신고는 된다)."""

    model_config = ConfigDict(from_attributes=True)

    ends_at: UtcDatetime
    reason: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    nickname: str
    created_at: UtcDatetime
    is_admin: bool
    avatar_url: str | None = None
    community_suspension: SuspensionInfo | None = None  # 정지 중이 아니면 없다


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenResponse(TokenPair):
    user: UserOut


class UserUpdate(BaseModel):
    nickname: Nickname


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: Password


class PasswordChangeResponse(TokenPair):
    message: str


# ── 냉장고 ───────────────────────────────────────────────────────────────

FridgeName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class FridgeCreate(BaseModel):
    name: FridgeName


Visibility = Literal["private", "public"]


class FridgeUpdate(BaseModel):
    """바꿀 것만 보낸다. name은 이름, visibility는 멤버가 아닌 사람에게 보일 범위(private 나만 / public 로그인한 모두가 읽기만)."""

    name: FridgeName | None = None
    visibility: Visibility | None = None

    @model_validator(mode="after")
    def _something_to_change(self):
        if self.name is None and self.visibility is None:
            raise ValueError("바꿀 내용(name 또는 visibility)이 없습니다.")
        return self


class FridgeOut(BaseModel):
    id: int
    name: str
    owner_id: int
    my_role: str
    member_count: int
    created_at: UtcDatetime
    visibility: Visibility


class MemberOut(BaseModel):
    user_id: int
    username: str
    nickname: str
    role: str
    joined_at: UtcDatetime


class FridgeDetail(FridgeOut):
    members: list[MemberOut]


# ── 식품 ─────────────────────────────────────────────────────────────────


class ItemCreate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    name: ItemName
    quantity: float = Field(1, gt=0)
    unit: Unit = "개"
    category: CategoryKey = None
    storage: Storage | None = None
    barcode: Barcode = None
    unit_price: int | None = Field(None, ge=0)
    total_price: int | None = Field(None, ge=0)
    purchased_on: date | None = None
    expiry_date: date | None = None
    memo: str | None = Field(None, max_length=500)


class ItemUpdate(BaseModel):
    """보낸 필드만 수정한다. 가격·소비기한·메모는 null을 보내면 비운다."""

    model_config = ConfigDict(allow_inf_nan=False)

    name: ItemName | None = None
    category: CategoryKey = None
    storage: Storage | None = None
    quantity: float | None = Field(None, gt=0)
    remaining_quantity: float | None = Field(None, gt=0)
    unit: Unit | None = None
    unit_price: int | None = Field(None, ge=0)
    total_price: int | None = Field(None, ge=0)
    expiry_date: date | None = None
    memo: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _required_fields_not_null(self) -> "ItemUpdate":
        for name in ("name", "category", "storage", "quantity", "remaining_quantity", "unit"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} 값은 비울 수 없습니다.")
        return self


class ConsumeRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    quantity: float | None = Field(None, gt=0, description="비우면 남은 양 전부")


class ItemOut(SizeFields):
    model_config = ConfigDict(from_attributes=True)

    id: int
    fridge_id: int
    receipt_id: int | None
    name: str
    category: str
    storage: str
    barcode: str | None
    quantity: float
    remaining_quantity: float
    unit: str = "개"
    unit_price: int | None
    total_price: int | None
    purchased_on: date
    expiry_date: date | None
    expiry_source: str | None
    status: str
    memo: str | None
    added_by: int | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    finished_at: UtcDatetime | None

    @computed_field
    @property
    def category_label(self) -> str:
        return label_of(self.category)

    @computed_field
    @property
    def days_left(self) -> int | None:
        """오늘(Asia/Seoul) 기준 남은 일수. 지났으면 음수."""
        return None if self.expiry_date is None else (self.expiry_date - today()).days


# ── 영수증 ───────────────────────────────────────────────────────────────


class DraftItem(SizeFields):
    line_no: int
    name: str
    quantity: float
    unit_price: int | None = None
    amount: int
    discount: int = 0
    barcode: str | None = None
    category: str
    category_label: str
    storage: str
    is_food: bool
    include: bool
    expiry_date: date | None = None
    expiry_source: str | None = None
    product_name: str | None = None
    confidence: float = 0.0
    name_cut: bool = Field(False, description="쇼핑몰 주문내역 화면에서 이름이 잘려 있다. 확정 전에 이름을 확인하게 한다")


class ReceiptItemOut(SizeFields):
    model_config = ConfigDict(from_attributes=True)

    id: int
    line_no: int
    name: str
    barcode: str | None
    quantity: float
    unit: str = "개"
    unit_price: int | None
    amount: int
    discount: int
    category: str | None
    included: bool
    food_item_id: int | None

    @computed_field
    @property
    def category_label(self) -> str:
        return label_of(self.category)


class ReceiptSummary(BaseModel):
    id: int
    status: str
    store_name: str | None
    purchased_on: date | None
    total_amount: int | None
    item_count: int
    created_at: UtcDatetime
    confirmed_at: UtcDatetime | None


class ReceiptDetail(BaseModel):
    id: int
    fridge_id: int
    status: str
    store_name: str | None
    purchased_on: date | None
    total_amount: int | None
    receipt_barcode: str | None
    uploaded_by: int | None
    created_at: UtcDatetime
    confirmed_at: UtcDatetime | None
    draft_items: list[DraftItem] = Field(description="OCR로 읽은 초안. 확정 화면에서 수정해 confirm으로 보낸다")
    items: list[ReceiptItemOut] = Field(description="확정된 줄 (pending이면 빈 목록)")
    items_sum: int = Field(description="품목 실결제 합 (금액 - 할인)")
    warnings: list[str]
    raw_lines: list[str] = Field(description="OCR 원문 줄")


class BatchUploadResult(BaseModel):
    index: int = Field(description="보낸 순서 (0부터)")
    filename: str | None
    ok: bool
    status_code: int = Field(description="201이면 초안이 만들어졌다. 아니면 한 장 업로드가 돌려줬을 HTTP 상태 (409 중복, 503 바쁨 등)")
    receipt: ReceiptDetail | None = Field(None, description="ok일 때 초안")
    detail: str | None = Field(None, description="ok가 아닐 때 한국어 사유")
    receipt_id: int | None = Field(None, description="409(이미 등록됨)일 때 기존 영수증 번호")
    retry_after: int | None = Field(None, description="503·429일 때 이 초 뒤에 다시 올리면 된다")


class ReceiptBatchResponse(BaseModel):
    results: list[BatchUploadResult] = Field(description="보낸 순서대로. 한 장이 실패해도 나머지는 처리된다")
    succeeded: int
    failed: int


class ConfirmItem(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    line_no: int | None = Field(None, description="초안의 line_no. 보내면 초안과 대조해 소비기한 출처를 유지한다")
    name: ItemName
    quantity: float = Field(gt=0)
    unit: Unit = "개"
    unit_price: int | None = Field(None, ge=0)
    amount: int = Field(ge=0)
    discount: int = Field(0, ge=0)
    barcode: Barcode = None
    category: CategoryKey = None
    storage: Storage | None = None
    expiry_date: date | None = None
    include: bool

    @model_validator(mode="after")
    def _discount_within_amount(self) -> "ConfirmItem":
        if self.discount > self.amount:
            raise ValueError(f"'{self.name}'의 할인이 금액보다 큽니다.")
        return self


class ReceiptConfirmRequest(BaseModel):
    store_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    purchased_on: date | None = None
    items: list[ConfirmItem] = Field(min_length=1, max_length=300)


class ReceiptConfirmResponse(BaseModel):
    receipt_id: int
    created_items: list[ItemOut]
    skipped: int = Field(description="냉장고에 넣지 않은 줄 수 (구매 기록에는 남는다)")


# ── 바코드·카테고리 ──────────────────────────────────────────────────────


class BarcodeLookupOut(BaseModel):
    found: bool
    name: str | None = None
    food_type: str | None = None
    manufacturer: str | None = None
    shelf_life_text: str | None = None
    shelf_life_days: int | None = None
    suggested_category: str | None = None
    suggested_expiry_date: date | None = None
    source: Literal["cache", "api", "disabled", "error"]


class CategoryOut(BaseModel):
    key: str
    label: str
    storage: str | None
    default_days: int | None


# ── 구매 기록·통계 ───────────────────────────────────────────────────────


class PurchaseOut(SizeFields):
    id: int
    receipt_id: int | None
    store_name: str | None
    name: str
    category: str
    category_label: str
    quantity: float
    unit: str = "개"
    unit_price: int | None
    total_price: int | None
    purchased_on: date
    status: str


class PurchaseGroup(BaseModel):
    key: str
    name: str
    count: int = Field(description="산 횟수")
    quantity: float = Field(description="산 수량 합")
    total_spent: int


class PurchaseSummary(BaseModel):
    start: date | None
    end: date | None
    group_by: Literal["item", "category"]
    total_spent: int
    groups: list[PurchaseGroup]


class CategoryAmount(BaseModel):
    category: str
    category_label: str
    amount: int
    count: int


class DiscardedCategory(BaseModel):
    category: str
    category_label: str
    value: int
    quantity: float
    count: int


class MonthlyStats(BaseModel):
    year: int
    month: int
    food_spent: int
    non_food_spent: int
    receipt_count: int
    item_count: int
    spent_by_category: list[CategoryAmount]
    consumed_value: int
    discarded_value: int
    waste_rate: float = Field(description="버린 금액 / (먹은 금액 + 버린 금액)")
    top_discarded: list[DiscardedCategory]


# ── 공유 링크 · 남의 냉장고 · 커뮤니티 ──────────────────────────────────


class PublicUser(BaseModel):
    """다른 사람에게 보여도 되는 정보. 이메일·아이디는 넣지 않는다. 닉네임은 겹칠 수 있다."""

    id: int
    nickname: str
    avatar_url: str | None = None

    @classmethod
    def of(cls, user) -> "PublicUser":
        return cls(id=user.id, nickname=user.nickname, avatar_url=user.avatar_url)


class ShareLinkOut(BaseModel):
    """냉장고의 공유 링크. 냉장고마다 하나이고, 주인이 바꾸기 전까지 같다."""

    token: str
    url: str | None  # 서버에 SHARE_LINK_BASE가 설정되어 있을 때만. 아니면 앱이 token으로 링크를 만든다(QR은 이 주소가 있어야 만든다)


class ShareLinkPreview(BaseModel):
    """링크를 열었을 때 "○○님의 냉장고 [이름]에 들어갈까요?"를 보여 주는 용도."""

    fridge_id: int
    fridge_name: str
    owner: PublicUser
    member_count: int
    already_member: bool  # 이미 멤버(주인 포함)면 들어오기 대신 바로 그 냉장고를 열면 된다


class JoinResult(BaseModel):
    fridge: FridgeOut  # 내 냉장고 목록(GET /fridges)의 한 줄과 같은 모양
    joined: bool  # 이번에 새로 들어왔으면 True, 이미 멤버였으면 False


class SharedFridgeOut(BaseModel):
    """남의 냉장고 목록 한 줄."""

    id: int
    name: str
    visibility: Visibility
    item_count: int
    my_permission: Literal["owner", "editor", "viewer"]  # viewer: 멤버가 아니라 '모두에게' 공개된 것을 구경만 한다


class SharedItemOut(SizeFields):
    """남의 냉장고에서 보이는 품목. 가격·메모·바코드·영수증·누가 넣었는지는 숨긴다."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str
    storage: str
    quantity: float
    remaining_quantity: float
    purchased_on: date
    expiry_date: date | None

    @computed_field
    @property
    def category_label(self) -> str:
        return label_of(self.category)

    @computed_field
    @property
    def days_left(self) -> int | None:
        return None if self.expiry_date is None else (self.expiry_date - today()).days


PostTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
PostBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
PostBoard = Literal["general", "recipe"]


class PostCreate(BaseModel):
    title: PostTitle
    body: PostBody
    board: PostBoard = "general"


class PostUpdate(BaseModel):
    title: PostTitle | None = None
    body: PostBody | None = None
    board: PostBoard | None = None

    @model_validator(mode="after")
    def supplied_fields_are_not_null(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("수정할 제목, 본문 또는 게시판을 입력해 주세요.")
        return self


class PostReviewOut(BaseModel):
    id: int
    recipe_id: str
    recipe_name: str
    rating: int


class PostPhotoOut(BaseModel):
    id: int
    url: str  # 로그인 토큰을 붙여 GET으로 받으면 사진이 온다


class LinkPreviewOut(BaseModel):
    """글에 붙인 첫 링크의 미리보기. 글을 쓰거나 고칠 때 서버가 만들어 저장한다. 누르면 url을 연다."""

    url: str
    kind: Literal["link", "youtube"]  # youtube면 영상 썸네일이다 (앱이 재생 표시를 얹는다)
    title: str | None = None
    description: str | None = None  # youtube면 채널 이름
    image: str | None = None  # https 주소만 온다
    site: str | None = None  # 사이트 이름 (없으면 도메인)


class PostOut(BaseModel):
    id: int
    author: PublicUser
    title: str
    body: str
    board: PostBoard
    link_preview: LinkPreviewOut | None = None  # 본문(없으면 제목)에서 처음 나온 링크. 링크가 없거나 못 만들면 없다
    review: PostReviewOut | None
    updated_at: UtcDatetime | None
    can_edit: bool
    created_at: UtcDatetime
    mine: bool  # 내가 쓴 글
    can_delete: bool  # 내가 쓴 글이거나 내가 관리자
    like_count: int
    liked_by_me: bool
    comment_count: int
    photos: list[PostPhotoOut]


class PostList(BaseModel):
    posts: list[PostOut]
    next_before_id: int | None  # 다음 쪽은 before_id=이 값으로 부른다. 없으면 마지막이다


class LikeOut(BaseModel):
    like_count: int
    liked_by_me: bool


CommentBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class CommentCreate(BaseModel):
    body: CommentBody


class CommentOut(BaseModel):
    id: int
    post_id: int
    author: PublicUser
    body: str
    created_at: UtcDatetime
    mine: bool
    can_delete: bool  # 내가 쓴 댓글이거나 내가 관리자


class CommentList(BaseModel):
    comments: list[CommentOut]  # 오래된 것부터
    next_after_id: int | None  # 다음 쪽은 after_id=이 값으로 부른다. 없으면 마지막이다


ReportReason = Literal["spam", "abuse", "inappropriate", "privacy", "other"]  # 광고·도배 / 욕설·괴롭힘 / 부적절·불쾌 / 개인정보 / 기타


class ReportCreate(BaseModel):
    reason: ReportReason
    detail: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)] | None = None


class ReportTargetOut(BaseModel):
    """신고된 글, 댓글, 레시피 후기. 관리자가 지우기 전에 내용을 확인하는 용도."""

    post_id: int | None = None  # 글과 댓글이면 그 글 번호. 후기면 없다
    recipe_id: str | None = None  # 후기면 그 레시피 번호
    recipe_name: str | None = None
    author: PublicUser
    author_is_admin: bool = False  # 관리자는 정지할 수 없다
    author_suspended_until: UtcDatetime | None = None  # 작성자가 지금 임시 정지 중이면 그 끝나는 때
    text: str  # 글이면 제목과 본문, 댓글이면 본문, 후기면 별점과 본문 (앞부분)
    photo_count: int
    deleted: bool


class AdminReportOut(BaseModel):
    id: int
    target_type: Literal["post", "comment", "review"]
    target_id: int
    reason: ReportReason
    detail: str | None
    status: Literal["open", "resolved", "dismissed"]
    created_at: UtcDatetime
    reporter: PublicUser
    open_reports_on_target: int  # 같은 대상에 아직 처리하지 않은 신고 수
    target: ReportTargetOut | None


class ReportResolve(BaseModel):
    action: Literal["delete", "dismiss"]  # 대상을 지운다 / 문제없음으로 닫는다


class SuspendCreate(BaseModel):
    """커뮤니티 임시 정지. 지금부터 days일 동안 글·댓글·후기를 쓸 수 없다."""

    days: int = Field(ge=1, le=365, description="정지할 일수 (지금부터)")
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)] = Field(
        description="사유. 정지된 사람에게도 그대로 보인다"
    )


class SuspensionOut(BaseModel):
    id: int
    user: PublicUser
    reason: str
    created_at: UtcDatetime
    ends_at: UtcDatetime
    created_by: PublicUser | None  # 정지한 관리자 (계정이 없어졌으면 없다)
    status: Literal["active", "ended", "lifted"]  # 정지 중 / 기간이 끝남 / 관리자가 풂
    lifted_at: UtcDatetime | None



# ── 레시피 별점·후기 ─────────────────────────────────────────────────────


def _blank_to_none(value: object) -> object:
    return None if isinstance(value, str) and not value.strip() else value


ReviewBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ReviewWrite(BaseModel):
    rating: int = Field(ge=1, le=5, description="별점 1~5")
    body: Annotated[ReviewBody | None, BeforeValidator(_blank_to_none)] = Field(None, description="후기 글. 비우거나 공백이면 별점만 남긴다")


class RatingSummary(BaseModel):
    average: float | None  # 소수 첫째 자리까지. 별점이 하나도 없으면 None
    count: int
    distribution: dict[str, int]  # "1"~"5"별로 몇 명이 줬는지 (별점 막대그래프용)


class ReviewOut(BaseModel):
    id: int
    recipe_id: str
    recipe_name: str
    author: PublicUser
    rating: int
    body: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime | None  # 고친 적이 없으면 None
    mine: bool
    can_delete: bool  # 내가 쓴 후기이거나 내가 관리자


class ReviewList(BaseModel):
    summary: RatingSummary
    my_review: ReviewOut | None  # 내가 이 레시피에 쓴 후기 (없으면 None). 목록에도 들어 있다
    reviews: list[ReviewOut]  # 최신순
    next_before_id: int | None  # 다음 쪽은 before_id=이 값으로 부른다. 없으면 마지막이다


class MyReviewList(BaseModel):
    reviews: list[ReviewOut]  # 최신순
    next_before_id: int | None


# ── 알림 아이콘 ──────────────────────────────────────────────────────────


class NotificationSummary(BaseModel):
    """아이콘에 붙일 숫자. 앱을 열 때와 돌아올 때 이것만 부르면 된다."""

    expiring_items: int  # 내가 멤버인 냉장고들에서 소비기한이 3일 이내로 남았거나 이미 지난 식품
    unread_comments: int  # 내 글에 달린 새 댓글
    total: int


class CommentNotificationOut(BaseModel):
    comment_id: int
    post_id: int
    post_title: str
    commenter: PublicUser
    body: str
    created_at: UtcDatetime
    unread: bool


class CommentNotificationList(BaseModel):
    comments: list[CommentNotificationOut]  # 최신순
    next_before_id: int | None  # 다음 쪽은 before_id=이 값으로 부른다. 없으면 마지막이다
    unread_count: int


class CommentsRead(BaseModel):
    up_to_id: int | None = Field(None, ge=1, description="화면에 보여 준 가장 새 댓글 번호. 비우면 지금까지 달린 것 모두")


class ExpiringItemOut(BaseModel):
    """알림에 보이는 소비기한 임박 식품. 내가 멤버인 냉장고들의 것이다."""

    item_id: int
    fridge_id: int
    fridge_name: str
    name: str
    category: str
    category_label: str
    storage: str
    remaining_quantity: float
    expiry_date: date

    @computed_field
    @property
    def days_left(self) -> int:
        """오늘(Asia/Seoul) 기준 남은 일수. 지났으면 음수."""
        return (self.expiry_date - today()).days


class ExpiringItemList(BaseModel):
    items: list[ExpiringItemOut]  # 소비기한이 빠른 순 (이미 지난 것이 맨 앞)
    count: int  # limit에 잘리기 전의 전체 수
    alert_days: int  # 소비기한이 며칠 이내로 남으면 임박으로 보는지
