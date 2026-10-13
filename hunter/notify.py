"""Alert delivery through Telegram and/or e-mail (SMTP). Both are optional and free.

Environment variables:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO
Without any of them, alerts are only printed and written to the report.
"""

from __future__ import annotations

import json
import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage


def _telegram(text: str) -> bool:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    ok = True
    for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
        data = urllib.parse.urlencode({"chat_id": chat, "text": chunk,
                                       "disable_web_page_preview": "true"}).encode()
        try:
            with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage",
                                        data=data, timeout=20) as r:
                ok &= json.load(r).get("ok", False)
        except Exception as e:
            print(f"[notify] telegram failed: {e}")
            ok = False
    return ok


def _email(subject: str, text: str) -> bool:
    host, to = os.getenv("SMTP_HOST"), os.getenv("ALERT_EMAIL_TO")
    user, pw = os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")
    if not (host and to and user and pw):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(text)
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            s.login(user, pw)
            s.send_message(msg)
        return True
    except Exception as e:
        print(f"[notify] e-mail failed: {e}")
        return False


def send(subject: str, text: str) -> None:
    print(f"\n===== {subject} =====\n{text}\n")
    sent = [name for name, ok in (("telegram", _telegram(f"{subject}\n\n{text}")),
                                  ("email", _email(subject, text))) if ok]
    print(f"[notify] delivered via: {', '.join(sent) or 'console only'}")
