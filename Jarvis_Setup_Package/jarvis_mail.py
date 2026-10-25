"""
jarvis_mail.py - Real unread-mail briefing over IMAP.

Configuration lives in ~/.openjarvis/mail.ini (never commit it):

    [mail]
    host = imap.gmail.com
    port = 993
    user = you@gmail.com
    password = <app password, not your login password>

For Gmail create an App Password (Google Account -> Security -> 2-Step
Verification -> App passwords).  Until the file exists, jarvis_actions keeps
serving its offline placeholder, so nothing breaks.
"""

import configparser
import email
import email.header
import imaplib
import os
import time

import jarvis_state

CONFIG_FILE = os.path.expanduser("~/.openjarvis/mail.ini")
CACHE_SECONDS = 300
MAX_ITEMS = 5


def is_configured():
    try:
        return os.path.exists(CONFIG_FILE)
    except Exception:
        return False


def setup_hint():
    return ("Mail is not set up yet. Create " + CONFIG_FILE +
            " with host, port, user and password, then ask me again.")


def _read_config():
    parser = configparser.ConfigParser()
    if not parser.read(CONFIG_FILE, encoding="utf-8"):
        return None
    section = parser["mail"] if parser.has_section("mail") else parser.defaults()
    try:
        host = section.get("host")
        user = section.get("user")
        password = section.get("password")
        port = int(section.get("port", "993"))
    except Exception:
        return None
    if not (host and user and password):
        return None
    return {"host": host, "port": port, "user": user, "password": password}


def _decode(raw):
    if raw is None:
        return ""
    parts = email.header.decode_header(raw)
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                text = text.decode(charset or "utf-8", "ignore")
            except Exception:
                text = text.decode("utf-8", "ignore")
        out.append(text)
    return "".join(out).strip()


def _fetch():
    cfg = _read_config()
    if not cfg:
        return {"ok": False, "configured": False, "error": "no-config", "text": setup_hint()}

    conn = None
    try:
        conn = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
        conn.login(cfg["user"], cfg["password"])
        conn.select("INBOX")
        status, data = conn.search(None, "UNSEEN")
        if status != "OK":
            return {"ok": False, "configured": True, "error": "search-failed", "text": "Could not scan the inbox."}

        ids = (data[0] or b"").split()
        total = len(ids)
        items = []
        for msg_id in reversed(ids[-MAX_ITEMS:]):
            status, chunk = conn.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if status != "OK" or not chunk or chunk[0] is None:
                continue
            header = chunk[0][1] if isinstance(chunk[0], tuple) else chunk[0]
            msg = email.message_from_bytes(header if isinstance(header, bytes) else str(header).encode("utf-8"))
            items.append({
                "from": _decode(msg.get("From")),
                "subject": _decode(msg.get("Subject")) or "(no subject)",
                "date": _decode(msg.get("Date")),
            })

        text = f"You have {total} unread " + ("email" if total == 1 else "emails") + "."
        if items:
            bits = [f"{i + 1}. {it['from']}: {it['subject']}" for i, it in enumerate(items)]
            text += " " + " ".join(bits)
        elif total:
            text += f" Showing the latest {len(items)}."
        return {"ok": True, "configured": True, "count": total, "items": items, "text": text}
    except imaplib.IMAP4.error as exc:
        return {"ok": False, "configured": True, "error": f"imap-error: {exc}",
                "text": "Mail login failed. Check the host, user and app password in mail.ini."}
    except Exception as exc:
        return {"ok": False, "configured": True, "error": f"error: {exc}",
                "text": "Mail server unreachable right now."}
    finally:
        try:
            if conn is not None:
                conn.logout()
        except Exception:
            pass


def unread(force=False):
    if not force:
        cached, _ = jarvis_state.get_cache("mail", CACHE_SECONDS)
        if cached:
            return cached
    payload = _fetch()
    jarvis_state.put_cache("mail", payload)
    return payload


def briefing():
    """Spoken summary, or None when mail was never configured."""
    payload = unread()
    if not payload.get("ok"):
        return None
    return payload.get("text")


if __name__ == "__main__":
    print(unread(force=True).get("text"))
