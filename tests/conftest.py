import os

# app을 import하기 전에 테스트 환경을 고정한다 (.env 파일보다 환경 변수가 우선한다).
os.environ.update(
    {
        "DATABASE_URL": "sqlite://",
        "EMAIL_BACKEND": "console",
        "OCR_ENGINE": "none",
        "JWT_SECRET": "test-secret-0123456789-0123456789-0123456789",
        "FOODSAFETY_API_KEY": "",
        "AUTO_CREATE_TABLES": "false",
        "TIMEZONE": "Asia/Seoul",
        "MAX_UPLOAD_MB": "10",
        "DEV_TOOLS": "false",
        "OCR_DET_MAX_SIDE": "0",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import security  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services import email  # noqa: E402

security.BCRYPT_ROUNDS = 4  # 테스트 속도용 (운영 기본값은 12)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """테스트마다 빈 DB·빈 메일함·임시 업로드 폴더로 시작한다."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    email.OUTBOX.clear()
    app.dependency_overrides.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
