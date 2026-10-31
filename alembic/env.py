"""Alembic 환경. DB 주소는 앱과 같은 설정(DATABASE_URL)을 쓴다."""

from logging.config import fileConfig

from alembic import context

from app import models  # noqa: F401  (모델을 Base.metadata에 등록)
from app.config import settings
from app.database import Base, make_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = make_engine(settings.database_url)
    with connectable.connect() as connection:
        if connection.dialect.name == "sqlite":
            # SQLite에서 칸을 지우는 batch는 표를 새로 만들어 옮기고 옛 표를 지운다. 외래 키가 켜져 있으면 그때
            # ON DELETE CASCADE가 연결된 행(사용자를 옮기면 냉장고 전부)을 지운다. 트랜잭션 밖에서만 꺼지므로 먼저 끝낸다.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
