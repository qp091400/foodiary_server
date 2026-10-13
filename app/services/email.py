"""메일 발송. EMAIL_BACKEND=console이면 로그에 찍고 OUTBOX에 쌓는다 (개발·테스트용)."""

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)

# console 백엔드로 "보낸" 메일. 테스트에서 인증코드를 꺼내는 데 쓴다.
OUTBOX: list[dict[str, str]] = []
_OUTBOX_LIMIT = 100

_PURPOSE_LABELS = {
    "signup": "회원가입",
    "find_id": "아이디 찾기",
    "reset_password": "비밀번호 재설정",
}


def _send_smtp(to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.mail_from or settings.smtp_user
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


def send_verification_email(to: str, code: str, purpose: str) -> None:
    """BackgroundTasks에서 호출한다. 발송에 실패해도 예외를 올리지 않고 로그만 남긴다."""
    label = _PURPOSE_LABELS.get(purpose, "본인 확인")
    subject = f"[냉장고] {label} 인증코드"
    body = f"{label} 인증코드는 {code} 입니다.\n5분 안에 입력해 주세요.\n\n요청한 적이 없다면 이 메일을 무시하세요."

    if settings.email_backend.strip().lower() == "smtp":
        try:
            _send_smtp(to, subject, body)
        except Exception as exc:  # 인증코드가 든 본문은 로그에 남기지 않는다
            logger.error("메일 발송 실패 (to=%s, purpose=%s): %s", to, purpose, exc)
        return

    logger.info("[console 메일] to=%s subject=%s code=%s", to, subject, code)
    OUTBOX.append({"to": to, "purpose": purpose, "code": code, "subject": subject, "body": body})
    del OUTBOX[:-_OUTBOX_LIMIT]
