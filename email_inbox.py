"""
Email inbox (IMAP) poller — apne domain / hosting ke email accounts se
verification emails lao, OTP extract karo, dashboard par dikhao.

Catch-all wale setup ke liye perfect: anything@yourdomain.com par aane wale
OTP emails yahan aa jayenge, SMS messages ke saath hi.

Har 60 second mein naye emails check hote hain (background thread).
"""
import email as email_lib
import email.header
import hashlib
import imaplib
import json
import os
import re
import socket
import sqlite3
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, "email_accounts.json")
POLL_INTERVAL = 60
FETCH_LIMIT = 30       # har poll mein aakhri itne emails dekho
BODY_MAX = 3000        # DB mein itna body text rakho
CONNECT_TIMEOUT = 20

# Provider presets — user sirf email + password de, host/port auto-fill ho jaye
PRESETS = {
    "hostinger": {"name": "Hostinger", "host": "imap.hostinger.com", "port": 993, "ssl": True},
    "gmail": {"name": "Gmail (app password zaroori)", "host": "imap.gmail.com", "port": 993, "ssl": True},
    "outlook": {"name": "Outlook / Microsoft 365", "host": "outlook.office365.com", "port": 993, "ssl": True},
    "zoho": {"name": "Zoho", "host": "imappro.zoho.com", "port": 993, "ssl": True},
    "custom": {"name": "Custom IMAP", "host": "", "port": 993, "ssl": True},
}


def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    try:
        with open(ACCOUNTS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(accounts, f, indent=2)


def safe_accounts():
    """Frontend ke liye — password chhupa kar."""
    out = []
    for a in load_accounts():
        out.append({
            "label": a.get("label", "?"),
            "host": a.get("host", ""),
            "port": a.get("port", 993),
            "email": a.get("email", ""),
            "last_check": a.get("last_check", 0),
            "last_status": a.get("last_status", ""),
        })
    return out


def decode_header(value):
    if not value:
        return ""
    parts = []
    for text, charset in email_lib.header.decode_header(value):
        if isinstance(text, bytes):
            try:
                parts.append(text.decode(charset or "utf-8", "ignore"))
            except Exception:
                parts.append(text.decode("utf-8", "ignore"))
        else:
            parts.append(text)
    return "".join(parts).strip()


def strip_html(s):
    s = re.sub(r"<script.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<style.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def get_body(msg):
    """text/plain prefer karo, warna HTML strip kar ke."""
    plain, html = None, None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                continue
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, "ignore")
            except Exception:
                continue
            if ctype == "text/plain" and plain is None:
                plain = text
            elif ctype == "text/html" and html is None:
                html = text
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                text = payload.decode(charset, "ignore")
                if msg.get_content_type() == "text/html":
                    html = text
                else:
                    plain = text
        except Exception:
            pass
    if plain and plain.strip():
        return plain.strip()
    if html:
        return strip_html(html)
    return ""


def test_login(host, port, email_addr, password):
    """Account add karte waqt foran check — ghalat details par seedha error."""
    try:
        socket.setdefaulttimeout(CONNECT_TIMEOUT)
        m = imaplib.IMAP4_SSL(host, int(port))
        try:
            m.login(email_addr, password)
            m.logout()
            return True, "ok"
        except imaplib.IMAP4.error as e:
            try:
                m.logout()
            except Exception:
                pass
            return False, f"login failed: {e}".replace(str(password), "***")
    except Exception as e:
        return False, f"connect failed: {type(e).__name__}: {e}".replace(str(password), "***")
    finally:
        socket.setdefaulttimeout(None)


def poll_account(conn, account):
    """Ek account ke naye emails lao. Returns (added_count, status)."""
    from app import extract_otp  # circular import se bachne ke liye yahan
    host = account.get("host", "")
    port = int(account.get("port", 993))
    email_addr = account.get("email", "")
    password = account.get("password", "")
    label = account.get("label", email_addr or "?")

    try:
        socket.setdefaulttimeout(CONNECT_TIMEOUT)
        m = imaplib.IMAP4_SSL(host, port)
        m.login(email_addr, password)
        m.select("INBOX", readonly=True)
        typ, data = m.uid("SEARCH", None, "ALL")
        m.logout()
        uids = [int(u) for u in data[0].split()] if data and data[0] else []
    except Exception as e:
        return 0, f"error: {type(e).__name__}"
    finally:
        socket.setdefaulttimeout(None)

    if not uids:
        return 0, "ok (koi email nahi)"

    last_uid = int(account.get("last_uid", 0) or 0)
    # Pehli dafa: purane emails skip karo, sirf naya track karo
    new_uids = [u for u in uids[-FETCH_LIMIT:] if u > last_uid]
    if not new_uids:
        account["last_uid"] = max(uids)
        return 0, "ok"

    added = 0
    try:
        socket.setdefaulttimeout(CONNECT_TIMEOUT)
        m = imaplib.IMAP4_SSL(host, port)
        m.login(email_addr, password)
        m.select("INBOX", readonly=True)
        for uid in sorted(new_uids):
            try:
                typ, data = m.uid("FETCH", str(uid), "(RFC822)")
                if not data or not data[0]:
                    continue
                raw = data[0][1]
                msg = email_lib.message_from_bytes(raw)
                sender = decode_header(msg.get("From", "")) or "?"
                # sirf email address nikaalo display ke liye
                em = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", sender)
                sender_short = em.group(0) if em else sender[:60]
                subject = decode_header(msg.get("Subject", "")) or "(no subject)"
                date = msg.get("Date", "")
                body = get_body(msg)[:BODY_MAX]
                text = f"{subject}\n{body}"
                otp = extract_otp(text)

                h = hashlib.sha1(f"email|{label}|{uid}".encode()).hexdigest()
                exists = conn.execute(
                    "SELECT 1 FROM messages WHERE dedupe_hash = ?", (h,)).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT INTO messages (from_number, to_number, body, otp_code,"
                    " provider, received_at, dedupe_hash)"
                    " VALUES (?, ?, ?, ?, 'email', ?, ?)",
                    (sender_short, "EMAIL: " + label,
                     f"Subject: {subject}\nDate: {date}\n\n{body}",
                     otp, int(time.time()), h))
                added += 1
            except Exception:
                continue
        m.logout()
    except Exception as e:
        return added, f"error: {type(e).__name__}"
    finally:
        socket.setdefaulttimeout(None)

    conn.commit()
    account["last_uid"] = max(uids)
    return added, "ok"


def poll_once(db_path):
    """Saare email accounts ek dafa check karo. Returns {label: (count, status)}."""
    results = {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN dedupe_hash TEXT")
        conn.commit()
    except Exception:
        pass

    accounts = load_accounts()
    changed = False
    for account in accounts:
        label = account.get("label", "?")
        try:
            added, status = poll_account(conn, account)
        except Exception as e:
            added, status = 0, f"error: {type(e).__name__}"
        account["last_check"] = int(time.time())
        account["last_status"] = status
        changed = True
        results[label] = (added, status)
    if changed:
        save_accounts(accounts)
    conn.commit()
    conn.close()
    return results


def poll_loop(db_path, interval=POLL_INTERVAL):
    while True:
        try:
            poll_once(db_path)
        except Exception:
            pass
        time.sleep(interval)


def start_poller(db_path):
    t = threading.Thread(target=poll_loop, args=(db_path,), daemon=True)
    t.start()
    return t
