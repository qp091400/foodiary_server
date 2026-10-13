"""FastAPI 앱: 라우터 등록, CORS, lifespan."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import models  # noqa: F401  (create_all 전에 모델을 등록한다)
from app.config import DEFAULT_JWT_SECRET, settings
from app.database import Base, engine
from app.errors import register_error_handlers
from app.routers import auth, dev, fridges, items, products, receipts, stats, users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.jwt_secret == DEFAULT_JWT_SECRET and settings.email_backend.strip().lower() == "smtp":
        logger.warning(
            "JWT_SECRET이 기본값입니다. 실제 메일을 보내는 운영 환경이라면 반드시 길고 무작위인 값으로 바꾸세요."
        )
    if settings.auto_create_tables:
        Base.metadata.create_all(bind=engine)
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="냉장고 영수증 API",
    description="영수증 사진으로 식품을 등록하고 소비기한·지출·낭비를 관리하는 백엔드",
    version="0.1.0",
    lifespan=lifespan,
)

_origins = settings.cors_origin_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials="*" not in _origins,  # 와일드카드 출처와 credentials는 함께 쓸 수 없다
    allow_methods=["*"],
    allow_headers=["*"],
)
register_error_handlers(app)

for module in (auth, users, fridges, items, receipts, products, stats, dev):
    app.include_router(module.router)


@app.get("/health", tags=["상태"], summary="서버 상태 확인")
def health():
    return {"status": "ok"}
