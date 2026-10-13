"""환경 변수(.env) 기반 설정."""

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://fridge:fridge@localhost:5432/fridge"

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

    foodsafety_api_key: str = ""  # 비우면 바코드 조회 비활성
    cors_origins: str = "*"
    auto_create_tables: bool = True
    timezone: str = "Asia/Seoul"
    dev_tools: bool = False  # true면 로그인 없는 테스트 페이지(/dev/receipt-test)를 연다. 운영에서는 끈다

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()] or ["*"]


settings = Settings()
