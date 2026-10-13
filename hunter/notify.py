"""Alert delivery through Telegram and/or e-mail (SMTP). Both are optional and free.

Environment variables:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO
  WHATSAPP_PHONE (with country code, e.g. +5534...), CALLMEBOT_APIKEY
WhatsApp goes through CallMeBot, a free third-party service for personal use: you activate it
from your own phone and it can only message the number that activated it.
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


def _whatsapp(text: str) -> bool:
    phone, key = os.getenv("WHATSAPP_PHONE"), os.getenv("CALLMEBOT_APIKEY")
    if not (phone and key):
        return False
    # Keep messages short: CallMeBot passes the text in the URL, so long alerts are trimmed
    # and the full version goes by e-mail/Telegram and the report.
    if len(text) > 1500:
        text = text[:1450] + "\n[...] full details in reports/latest.md"
    q = urllib.parse.urlencode({"phone": phone.replace(" ", ""), "text": text, "apikey": key})
    try:
        with urllib.request.urlopen(f"https://api.callmebot.com/whatsapp.php?{q}", timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
        low = body.lower()
        # CallMeBot answers with a small HTML page. Failures start with "ERROR" (e.g. an invalid
        # API key); a successful request echoes the recipient and the text and says it was queued.
        failed = (r.status != 200 or body.lstrip().upper().startswith("ERROR")
                  or "apikey is invalid" in low or "invalid format" in low)
        if failed:
            print(f"[notify] whatsapp answered: {body[:600]}")
        return not failed
    except Exception as e:
        print(f"[notify] whatsapp failed: {e}")
        return False


def configured() -> list[str]:
    ch = []
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        ch.append("telegram")
    if all(os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "ALERT_EMAIL_TO")):
        ch.append("email")
    if os.getenv("WHATSAPP_PHONE") and os.getenv("CALLMEBOT_APIKEY"):
        ch.append("whatsapp")
    return ch


def send(subject: str, text: str) -> bool:
    """Send through every configured channel. Returns True if at least one delivered."""
    print(f"\n===== {subject} =====\n{text}\n")
    print(f"[notify] configured channels: {', '.join(configured()) or 'none'}")
    sent = [name for name, ok in (("telegram", _telegram(f"{subject}\n\n{text}")),
                                  ("email", _email(subject, text)),
                                  ("whatsapp", _whatsapp(f"*{subject}*\n\n{text}"))) if ok]
    print(f"[notify] delivered via: {', '.join(sent) or 'console only'}")
    return bool(sent)
