"""
Personal OTP Dashboard — sirf aapke liye.
Twilio / Plivo / Telnyx se aane wale SMS webhooks receive karta hai,
OTP code auto-extract karta hai, aur dashboard par dikhata hai.
"""
import os
import re
import sqlite3
import time
from datetime import datetime
from flask import Flask, request, jsonify, render_template, abort

import free_inbox
import email_inbox

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("OTP_DB", os.path.join(BASE_DIR, "otp.db"))
TOKEN = os.environ.get("OTP_TOKEN", "")  # khaali ho to auth off (sirf local testing ke liye)
PORT = int(os.environ.get("PORT", "5057"))
BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")  # Render/Railway par 0.0.0.0 set karo

app = Flask(__name__)

# ---------------------------------------------------------------- DB
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_number TEXT,
            to_number TEXT,
            body TEXT,
            otp_code TEXT,
            provider TEXT DEFAULT 'unknown',
            received_at INTEGER
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_to ON messages(to_number)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_time ON messages(received_at DESC)")
    conn.commit()
    conn.close()

# ---------------------------------------------------------------- OTP extract
def extract_otp(text: str):
    """SMS body se OTP code nikaalo. Pehle keyword ke paas dhoondo, phir fallback."""
    if not text:
        return None
    # 1) keyword ke aas-paas: "code is 482913", "OTP: 7392"
    m = re.search(
        r"(?:code|otp|pin|verification|verify|passcode)[^\d]{0,25}(\d[\d \-]{3,9}\d)",
        text, re.IGNORECASE)
    if m:
        return re.sub(r"[\s\-]", "", m.group(1))
    # 2) fallback: sab se lamba standalone 4-8 digit group
    candidates = re.findall(r"(?<!\d)(\d{4,8})(?!\d)", text)
    if candidates:
        return max(candidates, key=len)
    return None

# ---------------------------------------------------------------- auth
def check_auth():
    if not TOKEN:
        return True
    if request.args.get("token") == TOKEN:
        return True
    if request.headers.get("X-Token") == TOKEN:
        return True
    return False

# ---------------------------------------------------------------- routes
@app.route("/health")
def health():
    return jsonify({"ok": True, "time": int(time.time())})

@app.route("/webhook/sms", methods=["POST"])
def webhook_sms():
    """Twilio (form) + generic JSON (Plivo/Telnyx/custom) dono support."""
    provider = "unknown"
    from_number = to_number = body = None

    if request.is_json:
        data = request.get_json(force=True, silent=True) or {}
        # generic keys
        from_number = data.get("from") or data.get("from_number") or data.get("sender")
        to_number = data.get("to") or data.get("to_number") or data.get("receiver")
        body = data.get("body") or data.get("text") or data.get("message")
        # Telnyx style
        if not body and isinstance(data.get("data"), dict):
            payload = data["data"].get("payload", {})
            from_number = from_number or payload.get("from", {}).get("phone_number")
            to_number = to_number or payload.get("to", [{}])[0].get("phone_number")
            body = payload.get("text")
        provider = data.get("provider", "json")
    else:
        # Twilio / Plivo form-encoded
        from_number = request.form.get("From")
        to_number = request.form.get("To")
        body = request.form.get("Body") or request.form.get("Text")
        provider = "twilio" if "MessageSid" in request.form else "form"

    if not body:
        return jsonify({"ok": False, "error": "empty body"}), 400

    otp = extract_otp(body)
    conn = db()
    conn.execute(
        "INSERT INTO messages (from_number, to_number, body, otp_code, provider, received_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (from_number, to_number, body, otp, provider, int(time.time())),
    )
    conn.commit()
    conn.close()
    # Twilio ko khaali TwiML jawab chahiye hota hai
    if request.form.get("MessageSid"):
        return "<Response></Response>", 200, {"Content-Type": "text/xml"}
    return jsonify({"ok": True, "otp": otp})

@app.route("/")
def index():
    if not check_auth():
        abort(401)
    return render_template("dashboard.html", token=request.args.get("token", ""))

@app.route("/api/messages")
def api_messages():
    if not check_auth():
        abort(401)
    number = request.args.get("number", "").strip()
    limit = min(int(request.args.get("limit", 100)), 500)
    conn = db()
    if number:
        rows = conn.execute(
            "SELECT * FROM messages WHERE to_number = ? ORDER BY id DESC LIMIT ?",
            (number, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    numbers = [r["to_number"] for r in conn.execute(
        "SELECT DISTINCT to_number FROM messages WHERE to_number IS NOT NULL ORDER BY to_number").fetchall()]
    conn.close()
    msgs = []
    for r in rows:
        msgs.append({
            "id": r["id"],
            "from": r["from_number"],
            "to": r["to_number"],
            "body": r["body"],
            "otp": r["otp_code"],
            "provider": r["provider"],
            "source_url": r["source_url"] if "source_url" in r.keys() else None,
            "time": datetime.fromtimestamp(r["received_at"]).strftime("%d %b %H:%M:%S"),
        })
    return jsonify({"messages": msgs, "numbers": numbers})

@app.route("/api/messages/<int:msg_id>", methods=["DELETE"])
def api_delete(msg_id):
    if not check_auth():
        abort(401)
    conn = db()
    conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

# ------------------------------------------------------- free numbers
@app.route("/api/free-sites")
def api_free_sites():
    if not check_auth():
        abort(401)
    return jsonify({"sites": free_inbox.KNOWN_FREE_SITES})

@app.route("/api/free-inboxes", methods=["GET", "POST"])
def api_free_inboxes():
    if not check_auth():
        abort(401)
    if request.method == "GET":
        return jsonify({"inboxes": free_inbox.load_inboxes()})
    data = request.get_json(force=True, silent=True) or {}
    label = (data.get("label") or "").strip()
    url = (data.get("url") or "").strip()
    if not label or not url.startswith("http"):
        return jsonify({"ok": False, "error": "label aur sahi URL do"}), 400
    inboxes = free_inbox.load_inboxes()
    if any(i.get("url") == url for i in inboxes):
        return jsonify({"ok": False, "error": "ye URL pehle se added hai"}), 400
    inboxes.append({"label": label, "url": url})
    free_inbox.save_inboxes(inboxes)
    return jsonify({"ok": True})

@app.route("/api/free-inboxes/<int:idx>", methods=["DELETE"])
def api_free_inbox_delete(idx):
    if not check_auth():
        abort(401)
    inboxes = free_inbox.load_inboxes()
    if 0 <= idx < len(inboxes):
        inboxes.pop(idx)
        free_inbox.save_inboxes(inboxes)
    return jsonify({"ok": True})

@app.route("/api/free-inboxes/refresh", methods=["POST"])
def api_free_refresh():
    if not check_auth():
        abort(401)
    results = free_inbox.poll_once(DB_PATH)
    return jsonify({"ok": True, "results": results})

# ------------------------------------------------------- email inbox
@app.route("/api/email-presets")
def api_email_presets():
    if not check_auth():
        abort(401)
    return jsonify({"presets": email_inbox.PRESETS})

@app.route("/api/email-accounts", methods=["GET", "POST"])
def api_email_accounts():
    if not check_auth():
        abort(401)
    if request.method == "GET":
        return jsonify({"accounts": email_inbox.safe_accounts()})
    data = request.get_json(force=True, silent=True) or {}
    label = (data.get("label") or "").strip()
    host = (data.get("host") or "").strip()
    port = int(data.get("port") or 993)
    email_addr = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not label or not host or "@" not in email_addr or not password:
        return jsonify({"ok": False, "error": "label, host, email aur password sab do"}), 400
    accounts = email_inbox.load_accounts()
    if any(a.get("email", "").lower() == email_addr.lower() and a.get("host") == host for a in accounts):
        return jsonify({"ok": False, "error": "ye account pehle se added hai"}), 400
    ok, msg = email_inbox.test_login(host, port, email_addr, password)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    accounts.append({"label": label, "host": host, "port": port,
                     "email": email_addr, "password": password, "last_uid": 0})
    email_inbox.save_accounts(accounts)
    return jsonify({"ok": True})

@app.route("/api/email-accounts/<int:idx>", methods=["DELETE"])
def api_email_account_delete(idx):
    if not check_auth():
        abort(401)
    accounts = email_inbox.load_accounts()
    if 0 <= idx < len(accounts):
        accounts.pop(idx)
        email_inbox.save_accounts(accounts)
    return jsonify({"ok": True})

@app.route("/api/email-accounts/refresh", methods=["POST"])
def api_email_refresh():
    if not check_auth():
        abort(401)
    results = email_inbox.poll_once(DB_PATH)
    return jsonify({"ok": True, "results": results})

if __name__ == "__main__":
    init_db()
    free_inbox.start_poller(DB_PATH)
    print(f"free inbox poller started (har {free_inbox.POLL_INTERVAL}s)")
    email_inbox.start_poller(DB_PATH)
    print(f"email poller started (har {email_inbox.POLL_INTERVAL}s)")
    if not TOKEN:
        print("!! OTP_TOKEN khaali hai — dashboard bina password ke khula hai. Sirf local test ke liye theek.")
    app.run(host=BIND_HOST, port=PORT)
