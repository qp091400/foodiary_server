"""FastAPI 앱: 라우터 등록, CORS, lifespan."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import models  # noqa: F401  (create_all 전에 모델을 등록한다)
from app.config import DEFAULT_JWT_SECRET, settings
from app.database import Base, engine
from app.errors import register_error_handlers
from app.middleware import SecurityMiddleware
from app.routers import auth, dev, fridges, items, products, receipts, stats, users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.validate_for_startup()
    if settings.jwt_secret == DEFAULT_JWT_SECRET and settings.email_backend.strip().lower() == "smtp":
        logger.warning(
            "JWT_SECRET이 기본값입니다. 실제 메일을 보내는 운영 환경이라면 반드시 길고 무작위인 값으로 바꾸세요."
        )
    if settings.auto_create_tables:
        Base.metadata.create_all(bind=engine)
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    if settings.ocr_warmup:
        from app.services.ocr.paddle import get_paddle_engine

        logger.info("OCR 시작 검사: 모델 로딩 및 시험 추론")
        get_paddle_engine().warmup()
        logger.info("OCR 시작 검사 완료")
    yield


app = FastAPI(
    title="냉장고 영수증 API",
    description="영수증 사진으로 식품을 등록하고 소비기한·지출·낭비를 관리하는 백엔드",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None if settings.app_env == "production" else "/redoc",
    openapi_url=None if settings.app_env == "production" else "/openapi.json",
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
app.add_middleware(SecurityMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)

for module in (auth, users, fridges, items, receipts, products, stats, dev):
    app.include_router(module.router)


@app.get("/health", tags=["상태"], summary="서버 상태 확인")
def health():
    return {"status": "ok"}


@app.get("/ready", tags=["상태"], summary="DB와 OCR 준비 상태 확인")
def ready():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "unavailable"})
    if settings.ocr_warmup:
        from app.services.ocr.paddle import get_paddle_engine

        if not get_paddle_engine().ready:
            return JSONResponse(status_code=503, content={"status": "unavailable", "ocr": "unavailable"})
    return {"status": "ready", "database": "ok", "ocr": "ready" if settings.ocr_warmup else "not_checked"}
