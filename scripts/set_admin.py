"""사용자를 관리자로 만들거나 되돌린다. 관리자는 남의 커뮤니티 글도 지울 수 있다.

사용: python -m scripts.set_admin 이메일          관리자로 만들기
      python -m scripts.set_admin 이메일 --remove  관리자에서 빼기
DATABASE_URL이 가리키는 DB를 바꾼다. 앱 안에는 관리자를 만드는 API가 없다(스스로 관리자가 될 수 없게).
"""

import argparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import User


def set_admin(db: Session, email: str, admin: bool) -> User:
    user = db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))
    if user is None:
        raise ValueError("그 이메일로 가입한 사용자가 없습니다.")
    user.is_admin = admin
    db.commit()
    return user


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("email")
    parser.add_argument("--remove", action="store_true", help="관리자에서 뺀다")
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            user = set_admin(db, args.email, not args.remove)
        except ValueError as error:
            parser.exit(1, f"{error}\n")
    print(f"{user.username}: 관리자 {'아님' if args.remove else '맞음'}")


if __name__ == "__main__":
    main()
