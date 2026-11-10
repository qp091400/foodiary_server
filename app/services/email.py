"""메일 발송. EMAIL_BACKEND=console이면 로그에 찍고 OUTBOX에 쌓는다 (개발·테스트용).

인증코드 메일은 꾸민 HTML 본문과 글자만 있는 본문을 함께 보낸다(HTML을 못 그리는 메일 앱은 글자 본문을 보여 준다).
메일 앱은 <style>과 외부 그림을 막는 경우가 많아서 HTML은 표와 인라인 스타일만 쓰고 그림은 넣지 않는다.
"""

import html
import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from zoneinfo import ZoneInfo

from app.config import settings
from app.services.verification import CODE_TTL

logger = logging.getLogger(__name__)

# console 백엔드로 "보낸" 메일. 테스트에서 인증코드를 꺼내는 데 쓴다.
OUTBOX: list[dict[str, str]] = []
_OUTBOX_LIMIT = 100

_PURPOSE_LABELS = {
    "signup": "회원가입",
    "find_id": "아이디 찾기",
    "reset_password": "비밀번호 재설정",
}

SENDER_NAME = "Foodiary"  # 받은편지함에 보이는 보낸 사람 이름 (MAIL_FROM에 이름이 없을 때)
# 앱 화면과 같은 색 (foodiary_app의 src/features/service/theme.ts)
_PAGE, _CARD, _INK, _INK_SOFT, _MUTED, _LINE, _BORDER, _FIELD = (
    "#F5F3F0", "#FFFFFF", "#1C1A18", "#3D3934", "#6B655E", "#EFECE8", "#E3DFDA", "#FAF9F7",
)
_FONT = "'Apple SD Gothic Neo','Malgun Gothic','맑은 고딕','Noto Sans KR',sans-serif"


def _object_particle(word: str) -> str:
    """'회원가입을', '아이디 찾기를'처럼 받침에 맞는 목적격 조사."""
    last = word[-1] if word else ""
    has_final = "가" <= last <= "힣" and (ord(last) - ord("가")) % 28 != 0
    return "을" if has_final else "를"


def verification_mail(code: str, purpose: str, requested_at: str) -> tuple[str, str, str]:
    """인증코드 메일의 (제목, 글자 본문, HTML 본문)."""
    label = _PURPOSE_LABELS.get(purpose, "본인 확인")
    minutes = int(CODE_TTL.total_seconds() // 60)
    reason = f"{label}{_object_particle(label)} 위해 이메일 인증이 필요합니다."
    subject = f"[Foodiary] {label} 인증번호 안내"
    text = (
        "[Foodiary] 인증번호를 안내해 드립니다.\n\n"
        f"안녕하세요.\n{reason}\n아래 인증번호를 앱 화면에 입력해 주세요.\n\n"
        f"요청 내용: {label} 이메일 인증\n"
        f"인증번호: {code}\n"
        f"유효 시간: 요청 후 {minutes}분\n"
        f"요청 일시: {requested_at}\n\n"
        "본인이 요청하지 않았다면 이 메일을 무시하세요. 인증번호는 다른 사람에게 알려 주지 마세요.\n"
        "이 메일은 발신 전용이라 회신해도 답변드릴 수 없습니다."
    )

    e = html.escape
    cell = f"padding:13px 16px;border-bottom:1px solid {_LINE};font-size:14px;line-height:1.5;"
    head = f"{cell}width:96px;background:{_FIELD};color:{_MUTED};font-weight:600;white-space:nowrap;"

    def row(name: str, value: str) -> str:
        return f'<tr><th align="left" style="{head}">{name}</th><td style="{cell}color:{_INK};">{value}</td></tr>'

    code_html = f'<span style="font-size:28px;font-weight:800;letter-spacing:6px;color:{_INK};">{e(code)}</span>'
    body = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{e(subject)}</title>
</head>
<body style="margin:0;padding:0;background:{_PAGE};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{e(label)} 인증번호 {e(code)} · {minutes}분 안에 입력해 주세요.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_PAGE};">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;background:{_CARD};border:1px solid {_BORDER};border-radius:16px;font-family:{_FONT};color:{_INK};">
<tr><td style="padding:26px 32px 20px;border-bottom:1px solid {_LINE};font-size:22px;font-weight:800;letter-spacing:4px;color:{_INK};">FOODIARY</td></tr>
<tr><td style="padding:30px 32px 8px;">
<h1 style="margin:0 0 22px;font-size:22px;font-weight:700;line-height:1.45;color:{_INK};">[Foodiary] 인증번호를 안내해 드립니다.</h1>
<p style="margin:0 0 4px;font-size:15px;line-height:1.75;color:{_INK_SOFT};">안녕하세요.</p>
<p style="margin:0 0 26px;font-size:15px;line-height:1.75;color:{_INK_SOFT};">{e(reason)}<br>아래 인증번호를 앱 화면에 입력해 주세요.</p>
<p style="margin:0 0 10px;font-size:14px;font-weight:700;color:{_INK};">&#9679; 요청 정보</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;border-top:2px solid {_INK};">
{row("요청 내용", f"{e(label)} 이메일 인증")}
{row("인증번호", code_html)}
{row("유효 시간", f"요청 후 {minutes}분")}
{row("요청 일시", e(requested_at))}
</table>
<p style="margin:18px 0 28px;font-size:13px;line-height:1.75;color:{_MUTED};">· 위 인증번호를 {minutes}분 안에 입력해 주세요.<br>· 본인이 요청하지 않았다면 이 메일을 무시하세요. 인증번호는 다른 사람에게 알려 주지 마세요.</p>
</td></tr>
<tr><td align="center" style="padding:22px 32px 26px;border-top:1px solid {_LINE};font-size:12px;line-height:1.75;color:{_MUTED};">※ 이 메일은 발신 전용이라 회신해도 답변드릴 수 없습니다.<br>&copy; Foodiary</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""
    return subject, text, body


def _sender() -> str:
    address = settings.mail_from or settings.smtp_user
    return address if "<" in address else formataddr((SENDER_NAME, address))


def _send_smtp(to: str, subject: str, text: str, html_body: str) -> None:
    message = EmailMessage()
    message["From"] = _sender()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


def send_verification_email(to: str, code: str, purpose: str) -> None:
    """BackgroundTasks에서 호출한다. 발송에 실패해도 예외를 올리지 않고 로그만 남긴다."""
    requested_at = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M:%S")
    subject, body, html_body = verification_mail(code, purpose, requested_at)

    if settings.email_backend.strip().lower() == "smtp":
        try:
            _send_smtp(to, subject, body, html_body)
        except Exception as exc:  # 인증코드가 든 본문은 로그에 남기지 않는다
            logger.error("메일 발송 실패 (to=%s, purpose=%s): %s", to, purpose, exc)
        return

    logger.info("[console 메일] to=%s subject=%s code=%s", to, subject, code)
    OUTBOX.append({"to": to, "purpose": purpose, "code": code, "subject": subject, "body": body, "html": html_body})
    del OUTBOX[:-_OUTBOX_LIMIT]
