"""환경 변수(.env) 기반 설정."""

import re
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://fridge:fridge@localhost:5432/fridge"
    app_env: Literal["development", "test", "production"] = "development"
    trusted_hosts: str = "*"

    jwt_secret: str = DEFAULT_JWT_SECRET
    access_token_minutes: int = 30
    refresh_token_days: int = 14

    email_backend: str = "console"  # console | smtp
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""

    upload_dir: str = "uploads"
    max_upload_mb: int = 10

    ocr_engine: str = "paddle"  # paddle | none
    ocr_lang: str = "korean"
    # 글자 위치 검출에 쓰는 해상도(긴 변 px). 0이면 줄이지 않는다. 1280이면 휴대폰 사진에서 2배쯤 빨라진다.
    ocr_det_max_side: int = 0
    ocr_version: str = "PP-OCRv5"
    ocr_detection_model: str = "PP-OCRv5_server_det"
    ocr_recognition_model: str = "korean_PP-OCRv5_mobile_rec"
    ocr_textline_model: str = "PP-LCNet_x1_0_textline_ori"
    ocr_models_dir: str = ""  # Cloud Run: 검증한 모델을 이미지에 포함한 디렉터리
    ocr_cpu_threads: int = Field(2, ge=1, le=16)
    # 인텔 CPU 가속(oneDNN). 실제 영수증 6장에서 결과는 줄 단위로 같고 한 장 18~29초 → 1.4~2.8초, 최대 메모리 6.9GB → 1.9GB
    ocr_enable_mkldnn: bool = True
    ocr_warmup: bool = False
    ocr_fail_closed: bool = False
    rate_limit_enabled: bool = False
    rate_limit_scope: Literal["ip", "global"] = "ip"
    auth_requests_per_minute: int = Field(30, ge=1, le=1000)
    ocr_requests_per_minute: int = Field(10, ge=1, le=100)

    foodsafety_api_key: str = ""  # 비우면 바코드 조회 비활성
    cors_origins: str = "*"
    auto_create_tables: bool = True
    timezone: str = "Asia/Seoul"
    dev_tools: bool = False  # true면 로그인 없는 테스트 페이지(/dev/receipt-test)를 연다. 운영에서는 끈다
    # 설정하면 운영에서도 /receipt-test 페이지가 열린다. 인식 요청마다 이 비밀번호가 있어야 한다. 비우면 404
    receipt_test_password: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()] or ["*"]

    @property
    def trusted_host_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()] or ["*"]

    def validate_for_startup(self) -> None:
        """운영에서는 개발 기본값으로 서버를 공개하지 않는다. 비밀값은 오류에 넣지 않는다."""
        if self.ocr_engine.strip().lower() not in {"paddle", "none"}:
            raise ValueError("OCR_ENGINE must be paddle or none")
        if self.app_env != "production":
            return

        from sqlalchemy.engine import make_url
        from sqlalchemy.exc import ArgumentError

        errors = []
        if self.jwt_secret == DEFAULT_JWT_SECRET or len(self.jwt_secret.strip()) < 48:
            errors.append("JWT_SECRET must contain at least 48 characters")
        if self.dev_tools:
            errors.append("DEV_TOOLS must be false")
        if self.receipt_test_password and not re.fullmatch(r"[\x21-\x7e]{12,}", self.receipt_test_password):
            errors.append("RECEIPT_TEST_PASSWORD must be at least 12 ASCII characters without spaces")
        if self.auto_create_tables:
            errors.append("AUTO_CREATE_TABLES must be false; use Alembic")
        if "*" in self.cors_origin_list:
            errors.append("CORS_ORIGINS must name explicit origins")
        if any("*" in host for host in self.trusted_host_list):
            errors.append("TRUSTED_HOSTS must name explicit hosts")
        if self.ocr_engine.strip().lower() != "paddle" or not self.ocr_warmup or not self.ocr_fail_closed:
            errors.append("production requires OCR_ENGINE=paddle, OCR_WARMUP=true, OCR_FAIL_CLOSED=true")
        if not self.rate_limit_enabled:
            errors.append("RATE_LIMIT_ENABLED must be true")
        try:
            url = make_url(self.database_url)
            valid_database = url.get_backend_name() == "postgresql" and len(url.password or "") >= 24
        except ArgumentError:
            valid_database = False
        if not valid_database:
            errors.append("DATABASE_URL must use PostgreSQL with a password of at least 24 characters")
        if self.email_backend.strip().lower() != "smtp" or not all(
            (self.smtp_host, self.smtp_user, self.smtp_password, self.mail_from)
        ):
            errors.append("configure SMTP_HOST, SMTP_USER, SMTP_PASSWORD, MAIL_FROM and EMAIL_BACKEND=smtp")
        if errors:
            raise ValueError("Unsafe production configuration: " + "; ".join(errors))


settings = Settings()
