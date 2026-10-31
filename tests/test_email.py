"""인증코드 메일: 꾸민 HTML과 글자 본문을 함께 보내고, 제목·문구·보낸 사람 이름이 Foodiary로 나온다."""

import re

import pytest

from app.config import settings
from app.services import email
from app.services.email import send_verification_email, verification_mail

WHEN = "2026-10-31 11:46:18"


def test_the_mail_has_a_designed_html_part_and_a_plain_text_part():
    subject, text, page = verification_mail("114780", "find_id", WHEN)
    assert subject == "[Foodiary] 아이디 찾기 인증번호 안내"
    for part in (text, page):
        assert "114780" in part and "5분" in part and WHEN in part and "아이디 찾기" in part
        assert "본인이 요청하지 않았다면 이 메일을 무시하세요." in part
    assert "FOODIARY" in page and "[Foodiary] 인증번호를 안내해 드립니다." in page
    assert page.count("114780") == 2  # 받은편지함 미리 보기 한 줄과 본문 표
    assert '<html lang="ko">' in page and 'charset="utf-8"' in page


def test_the_html_uses_no_outside_files_or_style_sheets():
    _, _, page = verification_mail("114780", "signup", WHEN)
    assert not re.search(r"<img|<script|<link|<style|@import|url\(|src=|href=", page)  # 메일 앱이 막는 것은 쓰지 않는다


@pytest.mark.parametrize(
    "purpose, reason",
    [
        ("signup", "회원가입을 위해 이메일 인증이 필요합니다."),
        ("find_id", "아이디 찾기를 위해 이메일 인증이 필요합니다."),
        ("reset_password", "비밀번호 재설정을 위해 이메일 인증이 필요합니다."),
        ("something_else", "본인 확인을 위해 이메일 인증이 필요합니다."),
    ],
)
def test_the_sentence_uses_the_right_particle(purpose, reason):
    _, text, page = verification_mail("000000", purpose, WHEN)
    assert reason in text and reason in page


def test_values_are_escaped_in_the_html():
    _, _, page = verification_mail("<b>1</b>", "signup", "<i>now</i>")
    assert "<b>1</b>" not in page and "&lt;b&gt;1&lt;/b&gt;" in page and "&lt;i&gt;now&lt;/i&gt;" in page


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        FakeSMTP.sent.append(message)


@pytest.mark.parametrize("mail_from, shown", [("foodiary.app@gmail.com", "Foodiary <foodiary.app@gmail.com>"), ("푸디어리 <a@b.com>", "푸디어리 <a@b.com>")])
def test_smtp_sends_both_parts_with_a_sender_name(monkeypatch, mail_from, shown):
    FakeSMTP.sent.clear()
    monkeypatch.setattr(email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(settings, "email_backend", "smtp")
    monkeypatch.setattr(settings, "mail_from", mail_from)
    send_verification_email("minsu@example.com", "246810", "reset_password")
    message = FakeSMTP.sent[0]
    assert message.get_content_type() == "multipart/alternative"
    parts = {part.get_content_type(): part.get_content() for part in message.iter_parts()}
    assert set(parts) == {"text/plain", "text/html"} and all("246810" in body for body in parts.values())
    assert message["Subject"] == "[Foodiary] 비밀번호 재설정 인증번호 안내"
    assert str(message["From"]) == shown and message["To"] == "minsu@example.com"


def test_console_outbox_keeps_the_html_for_tests_and_development():
    send_verification_email("minsu@example.com", "135790", "signup")
    mail = email.OUTBOX[-1]
    assert mail["code"] == "135790" and "135790" in mail["body"] and "FOODIARY" in mail["html"]
