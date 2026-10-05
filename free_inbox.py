"""
Free public SMS inboxes (receive-smss.com, quackr.io, sms24.me waghera) ko
dashboard mein laane ka poller.

Sach: ye sites scraping ko rokti hain (Cloudflare / JS apps / unlock walls),
is liye ye poller "best effort" hai:
- jo site simple HTML deti hai, us ke messages auto-fetch ho jayenge
- jo block kare, us ke liye dashboard mein "Site kholo" button hai

Polling waqfa 90 second rakha hai taake sites par load na pare.
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INBOX_FILE = os.path.join(BASE_DIR, "free_numbers.json")
POLL_INTERVAL = 90

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

# Sites jo browser mein theek khulti hain — dashboard mein one-tap buttons ke liye
KNOWN_FREE_SITES = [
    {"name": "receive-smss.com", "url": "https://receive-smss.com/"},
    {"name": "quackr.io", "url": "https://quackr.io/temporary-numbers"},
    {"name": "sms24.me", "url": "https://sms24.me/en/numbers"},
    {"name": "PVAPins free", "url": "https://pvapins.com/free-numbers"},
]

TIME_RE = re.compile(r"(\d{1,2}:\d{2}(:\d{2})?|\d{1,2}\s?(seconds?|minutes?|hours?|days?)\s?ago|today|yesterday)", re.I)
SENDER_RE = re.compile(r"^[\+\d][\d\s\-\(\)]{5,18}$|^[A-Za-z0-9][A-Za-z0-9 \.\-_]{1,20}$")


def load_inboxes():
    if not os.path.exists(INBOX_FILE):
        return []
    try:
        with open(INBOX_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_inboxes(inboxes):
    with open(INBOX_FILE, "w") as f:
        json.dump(inboxes, f, indent=2)


def strip_tags(s):
    s = re.sub(r"<script.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<style.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_messages(html):
    """Generic parser — common static inbox structures. Returns [(sender, body, time)]."""
    out = []

    # Strategy 1: table rows (sab se common purani sites par)
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        cells = [strip_tags(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S | re.I)]
        cells = [c for c in cells if c]
        if len(cells) >= 2:
            body = max(cells, key=len)
            rest = [c for c in cells if c != body]
            sender = next((c for c in rest if SENDER_RE.match(c) and len(c) < 25), rest[0] if rest else "?")
            t = next((c for c in rest if TIME_RE.search(c)), "")
            if len(body) > 8:
                out.append((sender, body, t))

    # Strategy 2: repeated div blocks with sms/message-ish class
    if not out:
        blocks = re.findall(
            r'<div[^>]*class="[^"]*(?:sms|message|msg|inbox-item|list-item)[^"]*"[^>]*>(.*?)</div>',
            html, flags=re.S | re.I)
        # group consecutive blocks in threes: sender / body / time
        texts = [strip_tags(b) for b in blocks]
        texts = [t for t in texts if t]
        for i in range(0, len(texts), 3):
            chunk = texts[i:i + 3]
            if not chunk:
                continue
            body = max(chunk, key=len)
            rest = [c for c in chunk if c != body]
            sender = rest[0] if rest else "?"
            t = next((c for c in rest if TIME_RE.search(c)), "")
            if len(body) > 8:
                out.append((sender, body, t))

    # dedupe, newest guess last nahi — order as found
    seen, uniq = set(), []
    for m in out:
        k = (m[0], m[1])
        if k not in seen:
            seen.add(k)
            uniq.append(m)
    return uniq[:50]


def fetch_inbox(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype and "text" not in ctype:
            return None, "not-html"
        html = r.read(600_000).decode("utf-8", "ignore")
    if len(html) < 2000:
        return None, "empty"
    # Cloudflare challenge / JS wall detect
    low = html[:3000].lower()
    if "just a moment" in low and "challenge" in low:
        return None, "blocked-cloudflare"
    if "watch a short video to unlock" in low:
        return None, "blocked-unlock-wall"
    return html, "ok"


def msg_hash(label, sender, body):
    return hashlib.sha1(f"{label}|{sender}|{body}".encode()).hexdigest()


def poll_once(db_path):
    """Ek dafa saare free inboxes check karo. Returns {label: (count, status)}."""
    from app import extract_otp  # circular import se bachne ke liye yahan
    results = {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # source_url column purani DB mein add karo
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN source_url TEXT")
        conn.commit()
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN dedupe_hash TEXT")
        conn.commit()
    except Exception:
        pass

    for inbox in load_inboxes():
        label = inbox.get("label", "?")
        url = inbox.get("url", "")
        if not url:
            results[label] = (0, "no-url")
            continue
        try:
            html, status = fetch_inbox(url)
        except Exception as e:
            results[label] = (0, f"error: {type(e).__name__}")
            continue
        if status != "ok":
            results[label] = (0, status)
            continue
        msgs = parse_messages(html)
        added = 0
        for sender, body, t in msgs:
            h = msg_hash(label, sender, body)
            exists = conn.execute(
                "SELECT 1 FROM messages WHERE dedupe_hash = ?", (h,)).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO messages (from_number, to_number, body, otp_code, provider,"
                " received_at, source_url, dedupe_hash)"
                " VALUES (?, ?, ?, ?, 'free', ?, ?, ?)",
                (sender, "FREE: " + label, body, extract_otp(body),
                 int(time.time()), url, h))
            added += 1
        conn.commit()
        results[label] = (added, "ok")
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
