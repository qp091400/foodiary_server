"""Run every minute in a private Cloud Run job (no OCR startup or public HTTP hook)."""
import json
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database import engine
from app.services.push import dispatch


def main():
    # A dedicated connection retains the session advisory lock across commits.
    # Overlapping Scheduler runs exit without delivering the same event twice.
    with engine.connect() as connection:
        postgres = connection.dialect.name == "postgresql"
        locked = not postgres or connection.scalar(text("SELECT pg_try_advisory_lock(5066353268240413512)"))
        connection.commit()
        if not locked:
            print(json.dumps({"busy": True}))
            return
        try:
            with Session(bind=connection, expire_on_commit=False) as db:
                print(json.dumps(dispatch(db)))
        finally:
            connection.rollback()
            if postgres:
                connection.execute(text("SELECT pg_advisory_unlock(5066353268240413512)"))
                connection.commit()


if __name__ == "__main__":
    main()
