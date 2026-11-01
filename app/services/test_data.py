"""테스트용 계정(POST /auth/test-account)의 냉장고를 채우는 샘플 식품.

카테고리마다 1~2줄씩, 수량·단위·가격·보관 위치·소비기한이 서로 다르게 넣는다. 그래서 만들자마자 목록, 임박·지난 식품,
카테고리별 보기, 구매 기록, 월별 통계(먹음·버림)를 모두 눈으로 볼 수 있다.

날짜는 모두 "오늘" 기준 상대값이라 언제 만들어도 같은 모양이다(오늘 산 식품과 오늘 먹거나 버린 기록도 있어서 달 초에도 이번 달 기록이 비지 않는다). 소비기한이 카테고리 기본 일수와 맞아떨어지게 산 날을 잡은 것은
`expiry_source`가 "category"(자동 추정)이고, 맞지 않는 것(냉동 보관 등)은 "manual"(직접 입력)이다.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import ConsumptionLog, FoodItem
from app.services.expiry import CATEGORIES
from app.timeutil import today, utcnow


@dataclass(frozen=True)
class Sample:
    name: str
    category: str
    quantity: float
    unit: str  # 개 / g / kg / ml / L
    total: int  # 할인을 반영한 실결제액(원)
    left: int  # 소비기한까지 남은 일수. 0은 오늘까지, 음수는 이미 지남
    storage: str | None = None  # 카테고리 기본 보관 위치와 다를 때만 (fridge / freezer / room)
    unit_price: int | None = None  # "개" 단위에서 할인 전 단가. 비우면 실결제액 ÷ 수량
    bought: int | None = None  # 며칠 전에 샀나. 비우면 카테고리 기본 소비기한과 맞아떨어지는 날
    remaining: float | None = None  # 일부를 먹은 경우 남은 양. 먹은 만큼 "먹음" 기록이 같이 생긴다
    used_ago: int = 3  # 일부를 먹은 날(며칠 전)
    memo: str | None = None


@dataclass(frozen=True)
class Finished:
    """이미 다 먹었거나 버린 식품. 기록 화면의 먹음·버림 통계를 채운다."""

    name: str
    category: str
    quantity: float
    unit: str
    total: int
    bought: int  # 며칠 전에 샀나
    ago: int  # 며칠 전에 다 먹었거나 버렸나
    action: str  # consume / discard


SAMPLES: tuple[Sample, ...] = (
    # 유제품·달걀·두부
    Sample("서울우유 흰우유 1L", "milk", 2, "개", 5560, 4, unit_price=2780, remaining=1),
    Sample("바나나맛우유 240ml", "milk", 6, "개", 8500, 10, unit_price=1700, memo="오늘 구매, 1+1 행사 후 할인"),
    Sample("풀무원 플레인 요거트 450g", "yogurt", 1, "개", 3900, 8),
    Sample("블루베리 그릭 요거트 80g", "yogurt", 4, "개", 5400, 11, unit_price=1500),
    Sample("모짜렐라 슬라이스 치즈 20매", "cheese", 1, "개", 4980, 24),
    Sample("앵커 가염 버터 200g", "butter", 1, "개", 5500, 45),
    Sample("서울우유 생크림 500ml", "butter", 1, "개", 4200, 3, bought=2),
    Sample("계란 특란 30구", "egg", 30, "개", 8100, 20, unit_price=270, remaining=21, used_ago=4),
    Sample("구운 메추리알 20알", "egg", 20, "개", 3000, 12, unit_price=150),
    Sample("풀무원 부침두부 300g", "tofu", 2, "개", 3900, 3, unit_price=2200),
    Sample("순두부 350g", "tofu", 1, "개", 1800, 1),
    # 고기·수산물
    Sample("한우 불고기용 300g", "beef", 300, "g", 18900, 1),
    Sample("호주산 차돌박이 200g", "beef", 200, "g", 9800, 60, storage="freezer", bought=2),
    Sample("국내산 삼겹살 600g", "pork", 600, "g", 14400, 3),  # 오늘 구매
    Sample("앞다리살 500g", "pork", 500, "g", 7900, 0),  # 오늘까지
    Sample("닭가슴살 1kg", "chicken", 1, "kg", 9900, -1),  # 어제 끝남
    Sample("닭다리살 500g", "chicken", 500, "g", 6500, 1),
    Sample("혼합 다짐육 400g", "minced", 400, "g", 6800, 1),
    Sample("스팸 클래식 340g", "processed_meat", 2, "개", 7960, 400, storage="room", unit_price=3980, bought=20),
    Sample("비엔나 소시지 300g", "processed_meat", 1, "개", 3900, 15),
    Sample("노르웨이 생연어 300g", "seafood", 300, "g", 12900, 1),
    Sample("손질 오징어 2마리", "seafood", 2, "개", 7000, 50, storage="freezer", unit_price=3500, bought=2),
    Sample("사각 어묵 250g", "fish_cake", 2, "개", 4500, 10, unit_price=2500),
    Sample("크래미 맛살 270g", "fish_cake", 1, "개", 2300, 12),
    # 김치·반찬·즉석조리
    Sample("종가집 배추김치 1kg", "kimchi", 1, "kg", 12900, 22),
    Sample("깍두기 500g", "kimchi", 500, "g", 5900, 25),
    Sample("시금치나물 무침 150g", "side_dish", 1, "개", 3500, 2),
    Sample("감자 샐러드 200g", "side_dish", 1, "개", 4200, 4),
    Sample("참치마요 삼각김밥", "ready_meal", 2, "개", 2800, 0, unit_price=1500),
    Sample("한우 사골곰탕 500g", "ready_meal", 1, "개", 4900, 1),
    # 채소·과일
    Sample("대파 1단", "leafy_veg", 1, "개", 2980, 4),  # 오늘 구매
    Sample("깻잎 100g", "leafy_veg", 100, "g", 1800, 2),
    Sample("무농약 오이 3입", "vegetable", 3, "개", 2700, 4, unit_price=900),
    Sample("방울토마토 500g", "vegetable", 1, "개", 5900, 5),
    Sample("국내산 양파 1.5kg", "root_veg", 1.5, "kg", 3980, 15),
    Sample("감자 2kg", "root_veg", 2, "kg", 5980, 12),
    Sample("부사 사과 5입", "fruit", 5, "개", 8900, 5, unit_price=1780),
    Sample("샤인머스캣 1kg", "fruit", 1, "kg", 12900, 3),
    Sample("딸기 500g", "soft_fruit", 500, "g", 8900, 4),  # 오늘 구매
    Sample("바나나 1송이", "soft_fruit", 1, "개", 3200, 2, storage="room"),
    # 빵·떡·냉동·음료
    Sample("우유 식빵 450g", "bread", 1, "개", 3500, 2, storage="room"),
    Sample("크루아상 4입", "bread", 4, "개", 5200, 25, storage="freezer", unit_price=1300, bought=1),
    Sample("가래떡 500g", "rice_cake", 1, "개", 4500, 1),
    Sample("냉동 군만두 1kg", "frozen", 1, "kg", 8900, 70),
    Sample("바닐라 아이스크림 474ml", "frozen", 2, "개", 8200, 80, unit_price=4500),
    Sample("국순당 생막걸리 750ml", "makgeolli", 2, "개", 3300, 6, unit_price=1650),
    Sample("삼다수 2L", "drink", 6, "개", 6600, 150, unit_price=1100),
    Sample("오렌지주스 1.5L", "drink", 1, "개", 4500, 12, storage="fridge", bought=8, memo="개봉함"),
    # 과자·즉석·통조림·양념·곡물·건어물
    Sample("새우깡 90g", "snack", 3, "개", 3600, 180, unit_price=1300),  # 오늘 구매
    Sample("초코파이 12입", "snack", 1, "개", 4980, 150),
    Sample("신라면 멀티 5입", "instant", 1, "개", 4480, 150),
    Sample("햇반 210g 12입", "instant", 1, "개", 13900, 120),
    Sample("동원 살코기 참치 150g", "canned", 4, "개", 11200, 330, unit_price=3100),
    Sample("스위트콘 340g", "canned", 2, "개", 4400, 310, unit_price=2200),
    Sample("청정원 순창 고추장 500g", "sauce", 1, "개", 6900, 340, storage="fridge", memo="개봉 후 냉장"),
    Sample("참기름 320ml", "sauce", 320, "ml", 9800, 320),
    Sample("신동진 쌀 10kg", "grain", 10, "kg", 32900, 320, remaining=7.5, used_ago=7),
    Sample("소면 500g", "grain", 1, "개", 2400, 330),
    Sample("조미 김 8봉", "dried", 8, "개", 4800, 150, unit_price=600),
    Sample("믹스넛 400g", "dried", 1, "개", 12900, 120),
)

FINISHED: tuple[Finished, ...] = (
    Finished("서울우유 흰우유 1L", "milk", 2, "개", 5560, 20, 14, "consume"),
    Finished("신라면 멀티 5입", "instant", 1, "개", 4480, 40, 25, "consume"),
    Finished("삼겹살 구이용 500g", "pork", 500, "g", 12000, 12, 10, "consume"),
    Finished("계란 특란 30구", "egg", 30, "개", 8100, 33, 20, "consume"),
    Finished("한우 국거리 400g", "beef", 400, "g", 21900, 6, 0, "consume"),
    Finished("시금치 200g", "leafy_veg", 200, "g", 2400, 9, 5, "discard"),
    Finished("연두부 300g", "tofu", 1, "개", 1900, 12, 4, "discard"),
    Finished("딸기 400g", "soft_fruit", 400, "g", 7900, 8, 0, "discard"),
)


def _unit_price(sample: Sample) -> int | None:
    """영수증 단가는 한 개 값이라 g·ml로 센 수량에는 맞지 않는다(식품 확정과 같은 규칙). 그때는 비워 둔다."""
    if sample.unit != "개":
        return None
    return sample.unit_price if sample.unit_price is not None else int(sample.total / sample.quantity + 0.5)


def bought_days(sample: Sample) -> int:
    """며칠 전에 샀나. 정하지 않았으면 카테고리 기본 소비기한이 남은 일수와 맞아떨어지는 날."""
    if sample.bought is not None:
        return sample.bought
    return (CATEGORIES[sample.category].days or 0) - sample.left


def expiry_source(sample: Sample) -> str:
    """산 날 + 카테고리 기본 일수가 소비기한이면 자동 추정(category), 아니면 직접 입력(manual)."""
    return "category" if bought_days(sample) + sample.left == CATEGORIES[sample.category].days else "manual"


def _value(total: int, quantity: float, used: float) -> int:
    return int(total / quantity * used + 0.5)


def seed_sample_fridge(db: Session, fridge_id: int, user_id: int, *, on: date | None = None) -> int:
    """냉장고에 샘플 식품(보관 중 + 다 먹음/버림)과 먹음·버림 기록을 넣는다. 커밋은 부르는 쪽이 한다. 넣은 식품 수를 돌려준다."""
    now = on or today()
    added = 0
    for sample in SAMPLES:
        item = FoodItem(
            fridge_id=fridge_id,
            name=sample.name,
            category=sample.category,
            storage=sample.storage or CATEGORIES[sample.category].storage or "room",
            quantity=sample.quantity,
            remaining_quantity=sample.quantity if sample.remaining is None else sample.remaining,
            unit=sample.unit,
            unit_price=_unit_price(sample),
            total_price=sample.total,
            purchased_on=now - timedelta(days=bought_days(sample)),
            expiry_date=now + timedelta(days=sample.left),
            expiry_source=expiry_source(sample),
            memo=sample.memo,
            added_by=user_id,
        )
        db.add(item)
        added += 1
        if sample.remaining is not None:
            db.flush()  # 먹음 기록에 식품 번호가 필요하다
            used = sample.quantity - sample.remaining
            db.add(
                ConsumptionLog(
                    fridge_id=fridge_id, food_item_id=item.id, item_name=sample.name, category=sample.category, user_id=user_id,
                    action="consume", quantity=used, value=_value(sample.total, sample.quantity, used), logged_on=now - timedelta(days=sample.used_ago),
                )
            )
    for done in FINISHED:
        finished_at: datetime = utcnow() - timedelta(days=done.ago)
        item = FoodItem(
            fridge_id=fridge_id,
            name=done.name,
            category=done.category,
            storage=CATEGORIES[done.category].storage or "room",
            quantity=done.quantity,
            remaining_quantity=0.0,
            unit=done.unit,
            unit_price=int(done.total / done.quantity + 0.5) if done.unit == "개" else None,
            total_price=done.total,
            purchased_on=now - timedelta(days=done.bought),
            expiry_date=now - timedelta(days=done.ago),
            expiry_source="category",
            status="consumed" if done.action == "consume" else "discarded",
            added_by=user_id,
            finished_at=finished_at,
        )
        db.add(item)
        db.flush()
        db.add(
            ConsumptionLog(
                fridge_id=fridge_id, food_item_id=item.id, item_name=done.name, category=done.category, user_id=user_id,
                action=done.action, quantity=done.quantity, value=done.total, logged_on=now - timedelta(days=done.ago),
            )
        )
        added += 1
    return added
