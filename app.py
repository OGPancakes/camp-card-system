import hashlib
import html
import json
import math
import mimetypes
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote_plus
from wsgiref.simple_server import make_server


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
DB_PATH = os.path.join(DATA_DIR, "camp_cards.db")
STARTING_WEEKLY_BALANCE = 147000.0
PORT = int(os.environ.get("PORT", "8123"))
HOST = os.environ.get("HOST", "127.0.0.1")
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "johhny")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
MARKET_REFRESH_HOURS = 12
DEFAULT_MARKET_PRICE = 1200.0
LIVE_MARKET_BUCKET_MINUTES = 15
LIVE_MARKET_MAX_SWING_PCT = 2.8
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
STUDENT_PHOTO_MAX_BYTES = 4 * 1024 * 1024


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def money(value):
    return f"${value:,.2f}"


def number(value):
    return f"{value:,.2f}"


def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def get_columns(conn, table_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS campers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            age INTEGER NOT NULL,
            card_number TEXT NOT NULL UNIQUE,
            balance REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camper_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL,
            actor_username TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (camper_id) REFERENCES campers(id)
        );

        CREATE TABLE IF NOT EXISTS staff_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS auth_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            FOREIGN KEY (staff_user_id) REFERENCES staff_users(id)
        );

        CREATE TABLE IF NOT EXISTS action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_user_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            details TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (staff_user_id) REFERENCES staff_users(id)
        );

        CREATE TABLE IF NOT EXISTS market_assets (
            symbol TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            sector TEXT NOT NULL,
            current_price REAL NOT NULL,
            previous_price REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            last_reason TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS market_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camper_id INTEGER NOT NULL,
            asset_symbol TEXT NOT NULL,
            shares REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (camper_id, asset_symbol),
            FOREIGN KEY (camper_id) REFERENCES campers(id),
            FOREIGN KEY (asset_symbol) REFERENCES market_assets(symbol)
        );

        CREATE TABLE IF NOT EXISTS market_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            summary TEXT NOT NULL,
            energy_level INTEGER NOT NULL DEFAULT 50,
            spirit_level INTEGER NOT NULL DEFAULT 50,
            weather_score INTEGER NOT NULL DEFAULT 50,
            competition_score INTEGER NOT NULL DEFAULT 50,
            submitted_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_symbol TEXT NOT NULL,
            price REAL NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'rule',
            created_at TEXT NOT NULL,
            FOREIGN KEY (asset_symbol) REFERENCES market_assets(symbol)
        );

        CREATE TABLE IF NOT EXISTS student_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camper_id INTEGER NOT NULL UNIQUE,
            login_name TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            photo_path TEXT NOT NULL DEFAULT '',
            photo_zoom REAL NOT NULL DEFAULT 1.0,
            photo_x REAL NOT NULL DEFAULT 50,
            photo_y REAL NOT NULL DEFAULT 50,
            banner_title TEXT NOT NULL DEFAULT '',
            banner_subtitle TEXT NOT NULL DEFAULT '',
            banner_theme TEXT NOT NULL DEFAULT 'violet',
            created_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (camper_id) REFERENCES campers(id)
        );

        CREATE TABLE IF NOT EXISTS student_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            FOREIGN KEY (student_user_id) REFERENCES student_users(id)
        );

        CREATE TABLE IF NOT EXISTS student_promos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camper_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'AVAILABLE',
            granted_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            redeemed_at TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (camper_id) REFERENCES campers(id)
        );

        CREATE TABLE IF NOT EXISTS voting_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS voting_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            camper_id INTEGER NOT NULL,
            card_number TEXT NOT NULL,
            camper_name TEXT NOT NULL,
            vote_value TEXT NOT NULL,
            cast_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT '',
            UNIQUE (session_id, camper_id),
            FOREIGN KEY (session_id) REFERENCES voting_sessions(id),
            FOREIGN KEY (camper_id) REFERENCES campers(id)
        );
        """
    )

    if "actor_username" not in get_columns(conn, "transactions"):
        conn.execute("ALTER TABLE transactions ADD COLUMN actor_username TEXT NOT NULL DEFAULT ''")
    student_columns = get_columns(conn, "student_users")
    if "photo_zoom" not in student_columns:
        conn.execute("ALTER TABLE student_users ADD COLUMN photo_zoom REAL NOT NULL DEFAULT 1.0")
    if "photo_x" not in student_columns:
        conn.execute("ALTER TABLE student_users ADD COLUMN photo_x REAL NOT NULL DEFAULT 50")
    if "photo_y" not in student_columns:
        conn.execute("ALTER TABLE student_users ADD COLUMN photo_y REAL NOT NULL DEFAULT 50")
    if "banner_title" not in student_columns:
        conn.execute("ALTER TABLE student_users ADD COLUMN banner_title TEXT NOT NULL DEFAULT ''")
    if "banner_subtitle" not in student_columns:
        conn.execute("ALTER TABLE student_users ADD COLUMN banner_subtitle TEXT NOT NULL DEFAULT ''")
    if "banner_theme" not in student_columns:
        conn.execute("ALTER TABLE student_users ADD COLUMN banner_theme TEXT NOT NULL DEFAULT 'violet'")

    for symbol, name, sector in [
        ("PIA", "Camp Spirit Index", "SPIRIT"),
        ("OIL", "Fuel & Logistics", "ENERGY"),
        ("GOLD", "Awards & Prestige", "VALUE"),
        ("TECH", "Innovation Lab", "TECH"),
    ]:
        existing_asset = conn.execute(
            "SELECT symbol FROM market_assets WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        if not existing_asset:
            conn.execute(
                """
                INSERT INTO market_assets (symbol, name, sector, current_price, previous_price, updated_at, last_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (symbol, name, sector, DEFAULT_MARKET_PRICE, DEFAULT_MARKET_PRICE, now(), "Opening market price"),
            )
            conn.execute(
                """
                INSERT INTO market_snapshots (asset_symbol, price, reason, source, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (symbol, DEFAULT_MARKET_PRICE, "Opening market price", "seed", now()),
            )

    admin_user = conn.execute(
        "SELECT id FROM staff_users WHERE username = ?",
        (ADMIN_USERNAME,),
    ).fetchone()
    if not admin_user:
        conn.execute(
            """
            INSERT INTO staff_users (username, password_hash, role, active, created_at)
            VALUES (?, ?, ?, 1, ?)
            """,
            (ADMIN_USERNAME, hash_password(ADMIN_PASSWORD), "ADMIN", now()),
        )

    conn.commit()
    conn.close()


def get_cookie_value(environ, key):
    cookie_header = environ.get("HTTP_COOKIE", "")
    if not cookie_header:
        return ""
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    morsel = cookie.get(key)
    return morsel.value if morsel else ""


def build_session_cookie(token):
    return f"camp_wallet_session={token}; Path=/; HttpOnly; SameSite=Lax"


def clear_session_cookie():
    return "camp_wallet_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def build_student_session_cookie(token):
    return f"camp_student_session={token}; Path=/; HttpOnly; SameSite=Lax"


def clear_student_session_cookie():
    return "camp_student_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"


def get_current_user(conn, environ):
    token = get_cookie_value(environ, "camp_wallet_session")
    if not token:
        return None
    return conn.execute(
        """
        SELECT staff_users.*
        FROM auth_sessions
        JOIN staff_users ON staff_users.id = auth_sessions.staff_user_id
        WHERE auth_sessions.token = ? AND staff_users.active = 1
        """,
        (token,),
    ).fetchone()


def get_current_student(conn, environ):
    token = get_cookie_value(environ, "camp_student_session")
    if not token:
        return None
    return conn.execute(
        """
        SELECT student_users.*, campers.name, campers.card_number, campers.balance, campers.active
        FROM student_sessions
        JOIN student_users ON student_users.id = student_sessions.student_user_id
        JOIN campers ON campers.id = student_users.camper_id
        WHERE student_sessions.token = ? AND campers.active = 1
        """,
        (token,),
    ).fetchone()


def parse_multipart(environ, raw_bytes, content_type):
    boundary_token = "boundary="
    if boundary_token not in content_type:
        return {}, {}
    boundary = content_type.split(boundary_token, 1)[1].strip().strip('"')
    delimiter = ("--" + boundary).encode("utf-8")
    fields = {}
    files = {}
    for part in raw_bytes.split(delimiter):
        part = part.strip()
        if not part or part == b"--":
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if b"\r\n\r\n" not in part:
            continue
        header_blob, body = part.split(b"\r\n\r\n", 1)
        body = body[:-2] if body.endswith(b"\r\n") else body
        headers = {}
        for line in header_blob.decode("utf-8", errors="ignore").split("\r\n"):
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        disposition = headers.get("content-disposition", "")
        disposition_bits = {}
        for chunk in disposition.split(";"):
            if "=" in chunk:
                key, value = chunk.split("=", 1)
                disposition_bits[key.strip().lower()] = value.strip().strip('"')
        field_name = disposition_bits.get("name", "")
        if not field_name:
            continue
        filename = disposition_bits.get("filename", "")
        if filename:
            files[field_name] = {
                "filename": os.path.basename(filename),
                "content_type": headers.get("content-type", "application/octet-stream"),
                "content": body,
            }
        else:
            fields[field_name] = body.decode("utf-8", errors="ignore").strip()
    return fields, files


def get_request_data(environ):
    size = int(environ.get("CONTENT_LENGTH") or 0)
    raw_bytes = environ["wsgi.input"].read(size)
    content_type = environ.get("CONTENT_TYPE") or ""
    if "multipart/form-data" in content_type.lower():
        return parse_multipart(environ, raw_bytes, content_type)
    raw = raw_bytes.decode("utf-8")
    parsed = parse_qs(raw)
    return ({key: values[0].strip() for key, values in parsed.items()}, {})


def redirect_response(start_response, location, cookie_header=None):
    headers = [("Location", location)]
    if cookie_header:
        headers.append(("Set-Cookie", cookie_header))
    start_response("302 Found", headers)
    return [b""]


def with_notice(path, message="", error="", action=""):
    bits = []
    if message:
        bits.append(f"message={quote_plus(message)}")
    if error:
        bits.append(f"error={quote_plus(error)}")
    if action:
        bits.append(f"action={quote_plus(action)}")
    if not bits:
        return path
    joiner = "&" if "?" in path else "?"
    return f"{path}{joiner}{'&'.join(bits)}"


def get_post_data(environ):
    fields, _ = get_request_data(environ)
    return fields


def log_action(conn, user, action_type, details):
    if not user:
        return
    conn.execute(
        """
        INSERT INTO action_log (staff_user_id, action_type, details, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (user["id"], action_type, details, now()),
    )


def archived_card_number(card_number, camper_id):
    safe_card = "".join(ch if ch.isalnum() else "_" for ch in card_number)
    return f"archived_{safe_card}_{camper_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"


def page_template(content, user=None, message="", error="", action=""):
    message_html = f'<div class="notice success">{html.escape(message)}</div>' if message else ""
    error_html = f'<div class="notice error">{html.escape(error)}</div>' if error else ""
    action_titles = {
        "create": "Card Created",
        "charge": "Charge Recorded",
        "add_funds": "Funds Added",
        "transfer": "Transfer Complete",
        "weekly_reset": "New Week Ready",
        "remove": "Camper Removed",
        "replace_card": "Card Swapped",
        "staff_created": "Staff Added",
        "market_refresh": "Market Refreshed",
        "market_buy": "Stock Purchased",
        "market_sell": "Stock Sold",
        "market_event": "Hype Saved",
        "student_created": "Student Portal Ready",
        "promo_granted": "Promo Added",
        "promo_redeemed": "Promo Redeemed",
        "student_linked": "Card Linked",
        "student_photo": "Photo Updated",
        "voting_start": "Voting Open",
        "voting_vote": "Vote Recorded",
        "voting_end": "Voting Closed",
    }
    action_title = action_titles.get(action, "Action Complete")
    action_html = (
        f"""
        <div class="action-banner action-{html.escape(action)}" aria-live="polite">
          <div class="action-burst"></div>
          <div class="action-copy">
            <span class="action-kicker">Camp Wallet Update</span>
            <strong>{html.escape(action_title)}</strong>
            <span>{html.escape(message)}</span>
          </div>
        </div>
        """
        if action and message
        else ""
    )
    topbar = ""
    if user:
        topbar = f"""
        <div class="topbar">
          <div>
            <strong>{html.escape(user["username"])}</strong>
            <span class="topbar-role">{html.escape(user["role"])}</span>
          </div>
          <form method="post" action="/logout" class="logout-form">
            <button type="submit">Log Out</button>
          </form>
        </div>
        """
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Camp Card System</title>
  <style>
    :root {{
      --bg: #09162d;
      --panel: rgba(255, 255, 255, 0.96);
      --ink: #102446;
      --accent: #c72d2d;
      --accent-dark: #961f1f;
      --line: rgba(16, 36, 70, 0.14);
      --gold: #f1c24b;
      --good: #0f5132;
      --good-bg: #d1fae5;
      --bad: #991b1b;
      --bad-bg: #fee2e2;
      --hero-ink: #f8fbff;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: "Trebuchet MS", "Gill Sans", sans-serif;
      color: var(--ink);
      background:
        linear-gradient(135deg, rgba(199, 45, 45, 0.28), transparent 30%),
        linear-gradient(225deg, rgba(15, 118, 110, 0.18), transparent 28%),
        linear-gradient(180deg, #0a1a34, #102446 38%, #ecf3ff 38%, #f7f9ff 100%);
      min-height: 100vh;
    }}
    .wrap {{
      width: min(1140px, calc(100% - 32px));
      margin: 24px auto 48px;
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
      padding: 14px 18px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.92);
      box-shadow: 0 12px 24px rgba(16, 36, 70, 0.08);
      backdrop-filter: blur(16px);
      animation: float-in 0.45s ease both;
    }}
    .topbar-role {{
      margin-left: 8px;
      padding: 4px 9px;
      border-radius: 999px;
      background: #e8eef9;
      font-size: 0.78rem;
      font-weight: bold;
    }}
    .logout-form {{
      margin: 0;
    }}
    .logout-form button {{
      margin: 0;
      width: auto;
      padding: 10px 14px;
    }}
    .hero {{
      position: relative;
      overflow: hidden;
      padding: 34px;
      border: 1px solid rgba(255, 255, 255, 0.18);
      border-radius: 28px;
      background: linear-gradient(135deg, rgba(199, 45, 45, 0.95), rgba(16, 36, 70, 0.98) 62%);
      box-shadow: 0 26px 48px rgba(7, 18, 39, 0.35);
      color: var(--hero-ink);
      animation: float-in 0.55s ease both;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      inset: auto -8% -30% auto;
      width: 280px;
      height: 280px;
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.08);
      filter: blur(4px);
      animation: soft-bob 7s ease-in-out infinite;
    }}
    h1, h2, h3 {{ margin-top: 0; }}
    h1 {{
      font-family: Georgia, "Times New Roman", serif;
      font-size: clamp(2.2rem, 4vw, 3.5rem);
      margin-bottom: 12px;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }}
    h2 {{
      font-family: Georgia, "Times New Roman", serif;
      font-size: 1.35rem;
      letter-spacing: 0.03em;
      text-transform: uppercase;
    }}
    .sub {{
      max-width: 780px;
      line-height: 1.5;
      font-size: 1.03rem;
      color: rgba(248, 251, 255, 0.92);
    }}
    .hero-top {{
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-columns: minmax(120px, 190px) 1fr;
      gap: 24px;
      align-items: center;
    }}
    .logo-shell {{
      padding: 14px;
      border-radius: 24px;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.16);
    }}
    .logo-shell img {{
      width: 100%;
      display: block;
      border-radius: 18px;
      background: #0b1730;
    }}
    .pill {{
      display: inline-block;
      padding: 6px 11px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.14);
      color: #fff;
      font-size: 0.84rem;
      font-weight: bold;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .hero-tagline {{
      display: inline-block;
      margin-top: 4px;
      color: rgba(255, 255, 255, 0.85);
      font-weight: bold;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      font-size: 0.82rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 18px;
      margin-top: 22px;
      align-items: stretch;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .stat, .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 20px;
      box-shadow: 0 14px 32px rgba(16, 36, 70, 0.08);
      animation: float-in 0.48s ease both;
      transition: transform 0.22s ease, box-shadow 0.22s ease, border-color 0.22s ease;
    }}
    .card:hover, .stat:hover, .ticker:hover {{
      transform: translateY(-2px);
      box-shadow: 0 18px 38px rgba(16, 36, 70, 0.12);
      border-color: rgba(16, 36, 70, 0.2);
    }}
    .stat {{
      padding: 16px;
      position: relative;
      overflow: hidden;
    }}
    .stat::before {{
      content: "";
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      width: 6px;
      background: linear-gradient(180deg, #c72d2d, #102446);
    }}
    .card {{
      padding: 20px;
      height: 100%;
      display: flex;
      flex-direction: column;
    }}
    .card h2 {{
      color: #102446;
      border-bottom: 2px solid #edf2fb;
      padding-bottom: 10px;
    }}
    .card form {{
      display: flex;
      flex-direction: column;
      flex: 1;
    }}
    label {{
      display: block;
      font-weight: bold;
      margin-bottom: 6px;
    }}
    input, select, button, textarea {{
      width: 100%;
      padding: 11px 12px;
      border-radius: 12px;
      border: 1px solid rgba(16, 36, 70, 0.18);
      font: inherit;
      margin-bottom: 12px;
      background: #fff;
    }}
    input:focus, select:focus, textarea:focus {{
      outline: 3px solid rgba(199, 45, 45, 0.16);
      border-color: rgba(199, 45, 45, 0.55);
    }}
    button {{
      background: linear-gradient(180deg, #d93737, #ad2525);
      color: #fff;
      border: 0;
      font-weight: bold;
      cursor: pointer;
      letter-spacing: 0.03em;
      text-transform: uppercase;
      transition: transform 0.18s ease, box-shadow 0.18s ease, background 0.18s ease;
      box-shadow: 0 10px 18px rgba(173, 37, 37, 0.16);
    }}
    button:hover {{
      background: var(--accent-dark);
      transform: translateY(-1px);
      box-shadow: 0 12px 22px rgba(173, 37, 37, 0.22);
    }}
    button:active {{ transform: translateY(0) scale(0.99); }}
    a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {{
      outline: 3px solid rgba(241, 194, 75, 0.48);
      outline-offset: 2px;
    }}
    .danger-button {{ background: linear-gradient(180deg, #7f1d1d, #5f1616); }}
    .danger-button:hover {{ background: #4a1010; }}
    .tiny {{
      font-size: 0.9rem;
      opacity: 0.82;
      margin-top: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
      font-size: 0.97rem;
      background: #fff;
      border-radius: 16px;
      overflow: hidden;
    }}
    th, td {{
      text-align: left;
      padding: 10px 12px;
      border-bottom: 1px solid #e3eaf7;
      vertical-align: top;
    }}
    th {{
      background: #e8eef9;
      color: #102446;
    }}
    .notice {{
      padding: 12px 14px;
      border-radius: 14px;
      margin: 18px 0;
      font-weight: bold;
    }}
    .success {{ background: var(--good-bg); color: var(--good); }}
    .error {{ background: var(--bad-bg); color: var(--bad); }}
    .compact-form {{ display: inline; }}
    .compact-form button {{
      width: auto;
      margin: 0;
      padding: 8px 12px;
      border-radius: 10px;
      font-size: 0.82rem;
    }}
    .action-cell {{ white-space: nowrap; }}
    .action-banner {{
      position: fixed;
      top: 18px;
      right: 18px;
      width: min(360px, calc(100% - 36px));
      padding: 18px;
      border-radius: 22px;
      color: #fff;
      overflow: hidden;
      z-index: 999;
      box-shadow: 0 24px 48px rgba(7, 18, 39, 0.28);
      animation: banner-in 0.45s ease, banner-out 0.45s ease 3.7s forwards;
    }}
    .action-copy {{
      position: relative;
      z-index: 2;
      display: grid;
      gap: 4px;
    }}
    .action-kicker {{
      font-size: 0.75rem;
      font-weight: bold;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      opacity: 0.88;
    }}
    .action-copy strong {{
      font-size: 1.25rem;
      font-family: Georgia, "Times New Roman", serif;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }}
    .action-burst {{
      position: absolute;
      right: -30px;
      top: -30px;
      width: 150px;
      height: 150px;
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.16);
      animation: burst-spin 3.2s linear infinite;
    }}
    .action-create {{ background: linear-gradient(135deg, #1d4ed8, #102446); }}
    .action-charge {{ background: linear-gradient(135deg, #c72d2d, #7f1d1d); }}
    .action-add_funds {{ background: linear-gradient(135deg, #0f766e, #14532d); }}
    .action-transfer {{ background: linear-gradient(135deg, #7c3aed, #102446); }}
    .action-weekly_reset {{ background: linear-gradient(135deg, #ea580c, #b45309); }}
    .action-remove {{ background: linear-gradient(135deg, #475569, #1e293b); }}
    .action-replace_card {{ background: linear-gradient(135deg, #2563eb, #0f172a); }}
    .action-staff_created {{ background: linear-gradient(135deg, #0891b2, #0f172a); }}
    .action-market_refresh {{ background: linear-gradient(135deg, #0f766e, #0b3b2e); }}
    .action-market_buy {{ background: linear-gradient(135deg, #1d4ed8, #0f172a); }}
    .action-market_sell {{ background: linear-gradient(135deg, #7c2d12, #431407); }}
    .action-market_event {{ background: linear-gradient(135deg, #ca8a04, #713f12); }}
    .action-voting_start {{ background: linear-gradient(135deg, #0f766e, #0f172a); }}
    .action-voting_vote {{ background: linear-gradient(135deg, #2563eb, #1e3a8a); }}
    .action-voting_end {{ background: linear-gradient(135deg, #7c2d12, #431407); }}
    .login-shell {{
      max-width: 480px;
      margin: 80px auto;
    }}
    .admin-grid {{
      display: grid;
      grid-template-columns: minmax(300px, 1fr) minmax(320px, 1.2fr);
      gap: 18px;
      margin-top: 22px;
    }}
    .login-note {{
      font-size: 0.92rem;
      opacity: 0.86;
    }}
    .tabbar {{
      display: flex;
      gap: 12px;
      margin-top: 22px;
      flex-wrap: wrap;
    }}
    .tablink {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 140px;
      padding: 12px 16px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.72);
      color: #102446;
      text-decoration: none;
      font-weight: bold;
      box-shadow: 0 10px 22px rgba(16, 36, 70, 0.08);
      transition: transform 0.18s ease, box-shadow 0.18s ease, background 0.18s ease;
    }}
    .tablink:hover {{
      transform: translateY(-2px);
      box-shadow: 0 14px 28px rgba(16, 36, 70, 0.12);
    }}
    .tablink.active {{
      background: linear-gradient(180deg, #d93737, #ad2525);
      color: #fff;
    }}
    .market-board {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
      margin-top: 22px;
    }}
    .ticker {{
      padding: 18px;
      border-radius: 20px;
      background: rgba(255, 255, 255, 0.96);
      border: 1px solid var(--line);
      box-shadow: 0 14px 32px rgba(16, 36, 70, 0.08);
      animation: float-in 0.5s ease both;
      transition: transform 0.22s ease, box-shadow 0.22s ease, border-color 0.22s ease;
    }}
    .ticker h3 {{
      margin-bottom: 8px;
      font-size: 1.1rem;
    }}
    .ticker-meta {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin: 8px 0;
    }}
    .trend-up {{ color: #166534; font-weight: bold; }}
    .trend-down {{ color: #991b1b; font-weight: bold; }}
    .trend-flat {{ color: #334155; font-weight: bold; }}
    .mini {{
      font-size: 0.84rem;
      opacity: 0.82;
      line-height: 1.45;
    }}
    .chart-box {{
      height: 84px;
      margin: 10px 0 6px;
    }}
    .chart-box svg {{
      width: 100%;
      height: 100%;
      display: block;
    }}
    .student-hero {{
      background: linear-gradient(135deg, rgba(49, 25, 122, 0.96), rgba(105, 54, 221, 0.94) 55%, rgba(244, 246, 255, 0.88) 100%);
    }}
    .student-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 18px;
      margin-top: 22px;
    }}
    .student-shell {{
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      gap: 22px;
      margin-top: 22px;
      align-items: start;
    }}
    .student-sidebar {{
      position: sticky;
      top: 18px;
      padding: 20px;
      border-radius: 28px;
      color: #f9fbff;
      background: linear-gradient(180deg, #141022, #2f1f58 48%, #171b2e 100%);
      box-shadow: 0 24px 44px rgba(17, 12, 35, 0.35);
      overflow: hidden;
    }}
    .student-sidebar::after {{
      content: "";
      position: absolute;
      inset: auto -22% -12% auto;
      width: 180px;
      height: 180px;
      border-radius: 50%;
      background: rgba(173, 143, 255, 0.2);
      filter: blur(6px);
    }}
    .student-sidebar > * {{
      position: relative;
      z-index: 1;
    }}
    .student-main {{
      display: grid;
      gap: 18px;
      position: relative;
      z-index: 2;
    }}
    .student-banner {{
      position: relative;
      overflow: visible;
      padding: 24px;
      border-radius: 30px;
      color: #fdfdff;
      box-shadow: 0 26px 52px rgba(49, 25, 122, 0.2);
      animation: float-in 0.7s ease;
    }}
    .banner-violet {{
      background: linear-gradient(135deg, #4c25cb, #8458ff 58%, #cdbdff);
    }}
    .banner-ocean {{
      background: linear-gradient(135deg, #0f4c81, #2d7dd2 58%, #8bd3ff);
    }}
    .banner-sunset {{
      background: linear-gradient(135deg, #912d5d, #d45b56 58%, #ffd29d);
    }}
    .banner-emerald {{
      background: linear-gradient(135deg, #0f766e, #24a287 58%, #99f6e4);
    }}
    .student-banner::before {{
      content: "";
      position: absolute;
      right: -24px;
      top: -18px;
      width: 180px;
      height: 180px;
      border-radius: 34px;
      background: rgba(255, 255, 255, 0.12);
      transform: rotate(14deg);
    }}
    .student-rail {{
      display: grid;
      gap: 10px;
      margin-top: 18px;
    }}
    .rail-item {{
      padding: 12px 14px;
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.08);
      font-weight: bold;
      letter-spacing: 0.02em;
    }}
    .student-photo {{
      width: 124px;
      height: 124px;
      border-radius: 28px;
      object-fit: cover;
      border: 4px solid rgba(255, 255, 255, 0.18);
      box-shadow: 0 10px 24px rgba(7, 18, 39, 0.22);
      background: rgba(255, 255, 255, 0.18);
    }}
    .photo-placeholder {{
      display: grid;
      place-items: center;
      font-weight: bold;
      color: #fff;
    }}
    .list-stack {{
      display: grid;
      gap: 10px;
      margin-top: 12px;
    }}
    .list-item {{
      padding: 14px;
      border: 1px solid #e3eaf7;
      border-radius: 16px;
      background: #fff;
    }}
    .promo-chip {{
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      background: #fff3c4;
      color: #7a5200;
      font-weight: bold;
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .inline-actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }}
    .inline-actions input,
    .inline-actions select,
    .inline-actions button {{
      margin-bottom: 0;
      width: auto;
      min-width: 120px;
      flex: 1 1 140px;
    }}
    .crop-grid {{
      display: grid;
      gap: 10px;
      margin-top: 10px;
    }}
    .crop-preview {{
      position: relative;
      width: min(260px, 100%);
      aspect-ratio: 1 / 1;
      overflow: hidden;
      border-radius: 28px;
      background: linear-gradient(135deg, #ddd6fe, #eef2ff);
      border: 1px solid #d7ddf8;
      margin-bottom: 12px;
    }}
    .crop-preview img {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: cover;
      transform-origin: center center;
    }}
    .range-row {{
      display: grid;
      gap: 4px;
    }}
    .range-row input[type="range"] {{
      padding: 0;
    }}
    .tiny-button {{
      padding: 10px 12px;
      font-size: 0.82rem;
    }}
    .student-kpis {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 14px;
    }}
    .student-kpi {{
      padding: 16px;
      border-radius: 22px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(243, 244, 255, 0.92));
      border: 1px solid rgba(131, 105, 233, 0.14);
      box-shadow: 0 14px 32px rgba(86, 66, 177, 0.08);
      animation: float-in 0.7s ease;
    }}
    .student-kpi strong {{
      display: block;
      font-size: 0.85rem;
      color: #5b4aa7;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
    }}
    .student-kpi span {{
      font-size: 1.45rem;
      font-weight: bold;
      color: #171b2e;
    }}
    .soft-card {{
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.97), rgba(247, 248, 255, 0.95));
      border: 1px solid rgba(118, 93, 220, 0.12);
      box-shadow: 0 18px 40px rgba(86, 66, 177, 0.08);
    }}
    .promo-swipe {{
      display: grid;
      gap: 10px;
      margin-top: 12px;
    }}
    .swipe-track {{
      display: grid;
      gap: 8px;
    }}
    .swipe-status {{
      font-size: 0.82rem;
      font-weight: bold;
      color: #5b4aa7;
    }}
    .locked-button {{
      background: linear-gradient(180deg, #a9afc9, #7f87a8);
      cursor: not-allowed;
    }}
    .student-form-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 18px;
    }}
    .student-note {{
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(118, 93, 220, 0.08);
      color: #473789;
      font-weight: bold;
    }}
    .settings-toggle {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 46px;
      height: 46px;
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.16);
      color: #fff;
      font-size: 1.35rem;
      cursor: pointer;
      border: 1px solid rgba(255, 255, 255, 0.18);
      box-shadow: 0 12px 24px rgba(49, 25, 122, 0.18);
      appearance: none;
      -webkit-appearance: none;
    }}
    .settings-wrap {{
      position: relative;
      flex: 0 0 auto;
      z-index: 30;
    }}
    .settings-wrap[data-open="false"] .settings-card {{
      display: none;
    }}
    .settings-card {{
      position: absolute;
      top: calc(100% + 12px);
      right: 0;
      width: min(380px, calc(100vw - 48px));
      margin-top: 10px;
      padding: 22px;
      border-radius: 24px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(243, 244, 255, 0.96));
      border: 1px solid rgba(118, 93, 220, 0.14);
      box-shadow: 0 18px 40px rgba(86, 66, 177, 0.1);
      z-index: 40;
    }}
    .settings-card h3 {{
      margin-bottom: 14px;
      color: #2f1f58;
    }}
    .settings-card form {{
      display: grid;
      gap: 10px;
    }}
    .settings-section {{
      display: grid;
      gap: 10px;
      padding: 14px;
      border-radius: 18px;
      background: rgba(118, 93, 220, 0.06);
      border: 1px solid rgba(118, 93, 220, 0.08);
    }}
    .settings-section + .settings-section {{
      margin-top: 14px;
    }}
    .settings-section-title {{
      margin: 0;
      font-size: 0.92rem;
      font-weight: bold;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: #5b4aa7;
    }}
    .settings-help {{
      margin: 0;
      font-size: 0.88rem;
      line-height: 1.45;
      color: #534a71;
    }}
    .settings-card input[type="file"] {{
      padding: 10px;
      border-radius: 14px;
      background: #fff;
    }}
    .settings-card button {{
      margin-bottom: 0;
    }}
    .table-wrap {{
      overflow-x: auto;
      width: 100%;
    }}
    .table-wrap th,
    .table-wrap td {{
      white-space: nowrap;
      word-break: normal;
    }}
    .voting-shell {{
      display: grid;
      gap: 22px;
      margin-top: 22px;
    }}
    .voting-launchers {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 18px;
    }}
    .voting-link {{
      display: block;
      padding: 18px;
      border-radius: 20px;
      background: linear-gradient(135deg, rgba(16, 36, 70, 0.98), rgba(199, 45, 45, 0.92));
      color: #fff;
      text-decoration: none;
      box-shadow: 0 18px 36px rgba(16, 36, 70, 0.18);
    }}
    .voting-link strong {{
      display: block;
      margin-bottom: 6px;
      font-size: 1.08rem;
      letter-spacing: 0.03em;
      text-transform: uppercase;
    }}
    .voting-board {{
      display: grid;
      grid-template-columns: minmax(320px, 420px) minmax(0, 1fr);
      gap: 22px;
      align-items: start;
    }}
    .vote-buttons {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 6px;
    }}
    .yes-button {{
      background: linear-gradient(180deg, #16a34a, #166534);
    }}
    .no-button {{
      background: linear-gradient(180deg, #dc2626, #991b1b);
    }}
    .vote-status-panel {{
      padding: 18px;
      border-radius: 20px;
      background: linear-gradient(180deg, rgba(16, 36, 70, 0.06), rgba(16, 36, 70, 0.02));
      border: 1px solid rgba(16, 36, 70, 0.08);
      min-height: 184px;
    }}
    .vote-status-name {{
      margin: 10px 0 6px;
      font-size: 1.8rem;
      font-family: Georgia, "Times New Roman", serif;
      line-height: 1.08;
      text-transform: uppercase;
    }}
    .vote-status-pill {{
      display: inline-block;
      padding: 7px 12px;
      border-radius: 999px;
      background: #e8eef9;
      color: #102446;
      font-size: 0.8rem;
      font-weight: bold;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .vote-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }}
    .vote-side {{
      padding: 20px;
      border-radius: 26px;
      min-height: 420px;
      color: #fff;
      box-shadow: 0 20px 42px rgba(16, 36, 70, 0.18);
    }}
    .vote-side.yes {{
      background: linear-gradient(180deg, #16a34a, #166534);
    }}
    .vote-side.no {{
      background: linear-gradient(180deg, #dc2626, #991b1b);
    }}
    .vote-side h2 {{
      color: #fff;
      border-bottom-color: rgba(255, 255, 255, 0.16);
      margin-bottom: 12px;
    }}
    .vote-big-number {{
      font-size: clamp(3rem, 6vw, 5rem);
      font-weight: bold;
      line-height: 1;
      margin-bottom: 8px;
    }}
    .vote-name-stack {{
      display: grid;
      gap: 10px;
      margin-top: 18px;
    }}
    .vote-name-card {{
      display: grid;
      gap: 4px;
      padding: 12px 14px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.12);
      border: 1px solid rgba(255, 255, 255, 0.12);
    }}
    .vote-name-card span {{
      font-size: 0.82rem;
      opacity: 0.84;
    }}
    .vote-name-card.empty {{
      background: rgba(255, 255, 255, 0.08);
    }}
    .projector-shell {{
      width: min(1380px, calc(100% - 32px));
      margin: 20px auto 40px;
    }}
    .projector-hero {{
      padding: 28px;
      border-radius: 30px;
      background: linear-gradient(135deg, rgba(16, 36, 70, 0.98), rgba(78, 98, 179, 0.92));
      color: #fff;
      box-shadow: 0 24px 48px rgba(16, 36, 70, 0.22);
    }}
    .projector-meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
      margin-top: 16px;
    }}
    .projector-meta-card {{
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.1);
      border: 1px solid rgba(255, 255, 255, 0.12);
    }}
    .projector-board {{
      margin-top: 22px;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }}
    .projector-board .vote-side {{
      min-height: 540px;
    }}
    .session-list {{
      display: grid;
      gap: 12px;
    }}
    .session-item {{
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(16, 36, 70, 0.04);
      border: 1px solid rgba(16, 36, 70, 0.08);
    }}
    @keyframes banner-in {{
      from {{ opacity: 0; transform: translateY(-16px) scale(0.96); }}
      to {{ opacity: 1; transform: translateY(0) scale(1); }}
    }}
    @keyframes banner-out {{
      to {{ opacity: 0; transform: translateY(-10px) scale(0.98); }}
    }}
    @keyframes burst-spin {{
      from {{ transform: rotate(0deg) scale(1); }}
      50% {{ transform: rotate(180deg) scale(1.08); }}
      to {{ transform: rotate(360deg) scale(1); }}
    }}
    @keyframes float-in {{
      from {{ opacity: 0; transform: translateY(18px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @keyframes soft-bob {{
      0%, 100% {{ transform: translateY(0) scale(1); }}
      50% {{ transform: translateY(-10px) scale(1.03); }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        scroll-behavior: auto !important;
        transition-duration: 0.01ms !important;
      }}
    }}
    @media (max-width: 860px) {{
      .admin-grid {{ grid-template-columns: 1fr; }}
      .student-shell {{ grid-template-columns: 1fr; }}
      .student-sidebar {{ position: static; }}
      .voting-board {{ grid-template-columns: 1fr; }}
      .projector-board {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 720px) {{
      .wrap {{ width: min(100% - 24px, 1140px); }}
      .hero {{ padding: 24px; }}
      .hero-top {{ grid-template-columns: 1fr; }}
      .logo-shell {{ max-width: 180px; }}
      .topbar {{ flex-direction: column; align-items: flex-start; gap: 10px; }}
    }}
  </style>
</head>
<body>
  {action_html}
  <div class="wrap">
    {topbar}
    {content}
  </div>
</body>
</html>"""


def render_login(message="", error=""):
    content = f"""
    <div class="login-shell">
      <section class="hero">
        <div class="hero-top">
          <div class="logo-shell">
            <img src="/assets/pia-logo.jpeg" alt="Camp logo">
          </div>
          <div>
            <span class="pill">Staff Access</span>
            <span class="hero-tagline">Camp Wallet Login</span>
            <h1>Camp Card System</h1>
            <p class="sub">Leaders and under leaders must sign in before using the wallet tools.</p>
          </div>
        </div>
      </section>
      {f'<div class="notice success">{html.escape(message)}</div>' if message else ''}
      {f'<div class="notice error">{html.escape(error)}</div>' if error else ''}
      <section class="card" style="margin-top: 22px;">
        <h2>Staff Login</h2>
        <form method="post" action="/login">
          <label for="username">Username</label>
          <input id="username" name="username" required>
          <label for="password">Password</label>
          <input id="password" name="password" type="password" required>
          <button type="submit">Log In</button>
        </form>
        <p class="login-note">Main admin account: <strong>{ADMIN_USERNAME}</strong> with password <strong>{ADMIN_PASSWORD}</strong>.</p>
      </section>
    </div>
    """
    return page_template(content, message=message, error=error)


def render_home(user, message="", error="", action="", tab=""):
    valid_tabs = {"bank", "stocks", "students", "voting"}
    active_tab = tab if tab in valid_tabs else ("students" if action.startswith("student_") or action.startswith("promo_") else "stocks" if action.startswith("market_") else "voting" if action.startswith("voting_") else "bank")
    conn = get_db()
    camper_count = conn.execute("SELECT COUNT(*) AS count FROM campers WHERE active = 1").fetchone()["count"]
    total_balance = conn.execute("SELECT COALESCE(SUM(balance), 0) AS total FROM campers WHERE active = 1").fetchone()["total"]
    transaction_count = conn.execute("SELECT COUNT(*) AS count FROM transactions").fetchone()["count"]
    student_count = conn.execute("SELECT COUNT(*) AS count FROM student_users").fetchone()["count"]
    available_promo_count = conn.execute(
        "SELECT COUNT(*) AS count FROM student_promos WHERE status = 'AVAILABLE'"
    ).fetchone()["count"]
    campers = conn.execute(
        """
        SELECT c.*,
               (
                   SELECT created_at
                   FROM transactions t
                   WHERE t.camper_id = c.id
                   ORDER BY t.id DESC
                   LIMIT 1
               ) AS last_activity
        FROM campers c
        WHERE c.active = 1
        ORDER BY c.name COLLATE NOCASE
        """
    ).fetchall()
    recent_transactions = conn.execute(
        """
        SELECT t.*, c.name, c.card_number
        FROM transactions t
        JOIN campers c ON c.id = t.camper_id
        ORDER BY t.id DESC
        LIMIT 15
        """
    ).fetchall()
    staff_users = conn.execute(
        "SELECT username, role, active, created_at FROM staff_users ORDER BY username COLLATE NOCASE"
    ).fetchall()
    action_logs = conn.execute(
        """
        SELECT action_log.*, staff_users.username
        FROM action_log
        JOIN staff_users ON staff_users.id = action_log.staff_user_id
        ORDER BY action_log.id DESC
        LIMIT 15
        """
    ).fetchall()
    student_accounts = conn.execute(
        """
        SELECT student_users.*, campers.name, campers.card_number, campers.balance
        FROM student_users
        JOIN campers ON campers.id = student_users.camper_id
        ORDER BY campers.name COLLATE NOCASE
        """
    ).fetchall()
    promo_rows = conn.execute(
        """
        SELECT student_promos.*, campers.name
        FROM student_promos
        JOIN campers ON campers.id = student_promos.camper_id
        ORDER BY student_promos.id DESC
        LIMIT 20
        """
    ).fetchall()
    active_vote_session = get_active_voting_session(conn)
    voting_data = voting_payload(conn, active_vote_session)
    recent_vote_sessions = get_recent_voting_sessions(conn, limit=8)
    market_assets = conn.execute(
        """
        SELECT symbol, name, sector, current_price, previous_price, updated_at, last_reason
        FROM market_assets
        ORDER BY symbol
        """
    ).fetchall()
    latest_event = conn.execute(
        "SELECT * FROM market_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    latest_market_updates = conn.execute(
        """
        SELECT asset_symbol, price, reason, source, created_at
        FROM market_snapshots
        ORDER BY id DESC
        LIMIT 8
        """
    ).fetchall()
    top_positions = conn.execute(
        """
        SELECT campers.name, market_positions.asset_symbol, market_positions.shares
        FROM market_positions
        JOIN campers ON campers.id = market_positions.camper_id
        WHERE campers.active = 1 AND market_positions.shares > 0
        ORDER BY market_positions.shares DESC, campers.name COLLATE NOCASE
        LIMIT 10
        """
    ).fetchall()
    live_assets = []
    history_by_symbol = {}
    for row in market_assets:
        live_price = live_market_price(row)
        history_points = [{"price": item["price"], "created_at": item["created_at"]} for item in get_market_history(conn, row["symbol"], limit=11)]
        history_points.append({"price": live_price, "created_at": now()})
        history_by_symbol[row["symbol"]] = history_points[-12:]
        live_assets.append({**dict(row), "live_price": live_price})
    conn.close()

    camper_options = "".join(
        f'<option value="{row["id"]}">{html.escape(row["name"])} | Card {html.escape(row["card_number"])}</option>'
        for row in campers
    ) or '<option value="">Add a camper in Bank first</option>'

    camper_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row["name"])}</td>
          <td>{row["age"]}</td>
          <td>{html.escape(row["card_number"])}</td>
          <td>{money(row["balance"])}</td>
          <td>{html.escape(row["last_activity"] or "No activity yet")}</td>
          <td class="action-cell">
            <form method="post" action="/campers/remove" class="compact-form">
              <input type="hidden" name="card_number" value="{html.escape(row["card_number"])}">
              <button type="submit" class="danger-button">Remove</button>
            </form>
          </td>
        </tr>
        """
        for row in campers
    ) or '<tr><td colspan="6">No campers added yet.</td></tr>'

    transaction_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row["created_at"])}</td>
          <td>{html.escape(row["actor_username"] or "Unknown")}</td>
          <td>{html.escape(row["name"])}</td>
          <td>{html.escape(row["card_number"])}</td>
          <td>{html.escape(row["kind"])}</td>
          <td>{money(row["amount"])}</td>
          <td>{html.escape(row["note"])}</td>
        </tr>
        """
        for row in recent_transactions
    ) or '<tr><td colspan="7">No transactions recorded yet.</td></tr>'

    student_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row["name"])}</td>
          <td>{html.escape(row["login_name"])}</td>
          <td>{html.escape(row["card_number"])}</td>
          <td>{"Yes" if row["photo_path"] else "No"}</td>
          <td>{money(row["balance"])}</td>
          <td>{html.escape(row["created_by"])}</td>
        </tr>
        """
        for row in student_accounts
    ) or '<tr><td colspan="6">No student portal accounts yet.</td></tr>'

    promo_table_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row["name"])}</td>
          <td>{html.escape(row["title"])}</td>
          <td>{html.escape(row["details"])}</td>
          <td>{html.escape(row["status"])}</td>
          <td>{html.escape(row["granted_by"])}</td>
          <td class="action-cell">{html.escape("Waiting for phone swipe" if row["status"] == "AVAILABLE" else row["redeemed_at"] or "Redeemed")}</td>
        </tr>
        """
        for row in promo_rows
    ) or '<tr><td colspan="6">No student promos yet.</td></tr>'
    session_rows = "".join(
        f"""
        <div class="session-item">
          <strong>{html.escape(row["title"])}</strong>
          <div class="mini">{html.escape(row["details"] or "Camp bill vote")}</div>
          <div class="mini"><strong>Status:</strong> {html.escape(row["status"])} | <strong>Yes:</strong> {row["yes_votes"]} | <strong>No:</strong> {row["no_votes"]}</div>
          <div class="mini"><strong>Started:</strong> {html.escape(row["started_at"])}</div>
        </div>
        """
        for row in recent_vote_sessions
    ) or '<div class="session-item">No voting sessions yet.</div>'

    ticker_cards = "".join(
        f"""
        <div class="ticker">
          <span class="pill" style="background:#102446;">{html.escape(row["symbol"])}</span>
          <h3>{html.escape(row["name"])}</h3>
          <div class="ticker-meta">
            <strong>{money(row["live_price"])}</strong>
            <span class="{'trend-up' if row['live_price'] > row['previous_price'] else 'trend-down' if row['live_price'] < row['previous_price'] else 'trend-flat'}">
              {'+' if row['live_price'] > row['previous_price'] else ''}{number(row["live_price"] - row["previous_price"])}
            </span>
          </div>
          <div class="chart-box">{market_chart_svg(history_by_symbol[row["symbol"]], "#c72d2d" if row["live_price"] >= row["previous_price"] else "#1d4ed8")}</div>
          <div class="mini"><strong>Sector:</strong> {html.escape(row["sector"])}</div>
          <div class="mini"><strong>Live drift:</strong> small moves every {LIVE_MARKET_BUCKET_MINUTES} minutes</div>
          <div class="mini"><strong>Big swing anchor:</strong> {html.escape(row["updated_at"])}</div>
          <div class="mini">{html.escape(row["last_reason"] or "No hype note yet.")}</div>
        </div>
        """
        for row in live_assets
    )
    market_update_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row["created_at"])}</td>
          <td>{html.escape(row["asset_symbol"])}</td>
          <td>{money(row["price"])}</td>
          <td>{html.escape(row["source"])}</td>
          <td>{html.escape(row["reason"])}</td>
        </tr>
        """
        for row in latest_market_updates
    ) or '<tr><td colspan="5">No market updates yet.</td></tr>'
    top_position_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row["name"])}</td>
          <td>{html.escape(row["asset_symbol"])}</td>
          <td>{number(row["shares"])}</td>
        </tr>
        """
        for row in top_positions
    ) or '<tr><td colspan="3">No student holdings yet.</td></tr>'

    admin_markup = ""
    if user["role"] == "ADMIN":
        staff_rows = "".join(
            f"""
            <tr>
              <td>{html.escape(row["username"])}</td>
              <td>{html.escape(row["role"])}</td>
              <td>{"Active" if row["active"] else "Disabled"}</td>
              <td>{html.escape(row["created_at"])}</td>
            </tr>
            """
            for row in staff_users
        ) or '<tr><td colspan="4">No staff accounts yet.</td></tr>'
        log_rows = "".join(
            f"""
            <tr>
              <td>{html.escape(row["created_at"])}</td>
              <td>{html.escape(row["username"])}</td>
              <td>{html.escape(row["action_type"])}</td>
              <td>{html.escape(row["details"])}</td>
            </tr>
            """
            for row in action_logs
        ) or '<tr><td colspan="4">No staff activity yet.</td></tr>'
        admin_markup = f"""
        <section class="admin-grid">
          <div class="card">
            <h2>Create Staff Login</h2>
            <form method="post" action="/staff/create">
              <label for="staff_username">Username</label>
              <input id="staff_username" name="username" required>
              <label for="staff_password">Password</label>
              <input id="staff_password" name="password" type="password" required>
              <label for="staff_role">Role</label>
              <select id="staff_role" name="role" required>
                <option value="LEADER">Leader</option>
                <option value="UNDER LEADER">Under Leader</option>
              </select>
              <button type="submit">Create Staff Account</button>
            </form>
            <p class="tiny">Only the main admin account can create new leader and under leader logins.</p>
          </div>

          <div class="card">
            <h2>Staff Accounts</h2>
            <table>
              <thead>
                <tr>
                  <th>Username</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>{staff_rows}</tbody>
            </table>
          </div>
        </section>

        <section class="card" style="margin-top: 22px;">
          <h2>Leader Activity Log</h2>
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Staff</th>
                <th>Action</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>{log_rows}</tbody>
          </table>
        </section>
        """

    bank_content = f"""
    <section class="grid">
      <div class="card">
        <h2>Add Camper Card</h2>
        <form method="post" action="/campers/add">
          <label for="name">Camper Name</label>
          <input id="name" name="name" required>
          <label for="age">Age</label>
          <input id="age" name="age" type="number" min="1" max="18" required>
          <label for="card_number">RFID Card Number</label>
          <input id="card_number" name="card_number" required>
          <label for="starting_balance">Starting Balance</label>
          <input id="starting_balance" name="starting_balance" type="number" min="0" step="0.01" value="{STARTING_WEEKLY_BALANCE:.2f}" required>
          <button type="submit">Create Camper</button>
        </form>
      </div>

      <div class="card">
        <h2>Charge Card</h2>
        <form method="post" action="/transactions/charge">
          <label for="charge_card_number">RFID Card Number</label>
          <input id="charge_card_number" name="card_number" required>
          <label for="charge_amount">Charge Amount</label>
          <input id="charge_amount" name="amount" type="number" min="0.01" step="0.01" required>
          <label for="charge_note">What Was Purchased?</label>
          <input id="charge_note" name="note" placeholder="Snack, soda, craft item..." required>
          <button type="submit">Charge Camper</button>
        </form>
      </div>

      <div class="card">
        <h2>Add Money</h2>
        <form method="post" action="/transactions/add-funds">
          <label for="fund_card_number">RFID Card Number</label>
          <input id="fund_card_number" name="card_number" required>
          <label for="fund_amount">Amount to Add</label>
          <input id="fund_amount" name="amount" type="number" min="0.01" step="0.01" required>
          <label for="fund_note">Reason</label>
          <input id="fund_note" name="note" value="Manual top-up" required>
          <button type="submit">Add Funds</button>
        </form>
      </div>

      <div class="card">
        <h2>Remove Camper Card</h2>
        <form method="post" action="/campers/remove">
          <label for="remove_card_number">RFID Card Number</label>
          <input id="remove_card_number" name="card_number" required>
          <button type="submit" class="danger-button">Remove Camper</button>
        </form>
        <p class="tiny">Removing a camper hides the account from active use but keeps old transactions for records.</p>
      </div>

      <div class="card">
        <h2>Transfer Between Cards</h2>
        <form method="post" action="/transactions/transfer">
          <label for="from_card_number">From RFID Card</label>
          <input id="from_card_number" name="from_card_number" required>
          <label for="to_card_number">To RFID Card</label>
          <input id="to_card_number" name="to_card_number" required>
          <label for="transfer_amount">Transfer Amount</label>
          <input id="transfer_amount" name="amount" type="number" min="0.01" step="0.01" required>
          <label for="transfer_note">Reason</label>
          <input id="transfer_note" name="note" value="Camper to camper transfer" required>
          <button type="submit">Transfer Money</button>
        </form>
        <p class="tiny">Use this when two campers are together and one wants to move part of their balance to the other.</p>
      </div>

      <div class="card">
        <h2>Replace Lost Card</h2>
        <form method="post" action="/campers/replace-card">
          <label for="replace_name">Camper Name</label>
          <input id="replace_name" name="name" required>
          <label for="replace_card_number">New RFID Card Number</label>
          <input id="replace_card_number" name="new_card_number" required>
          <button type="submit">Assign New Card</button>
        </form>
        <p class="tiny">Use this if a camper loses a card. Find them by name and scan or type the new card number.</p>
      </div>

      <div class="card">
        <h2>Find Camper By Card</h2>
        <form method="get" action="/lookup">
          <label for="lookup_card_number">RFID Card Number</label>
          <input id="lookup_card_number" name="card_number" required>
          <button type="submit">Lookup Card</button>
        </form>
        <p class="tiny">This is useful when a staff member scans or types a card number and wants to confirm the right camper before charging.</p>
      </div>

      <div class="card">
        <h2>Start New Week</h2>
        <form method="post" action="/weekly-reset">
          <label for="weekly_amount">Reset Every Camper To</label>
          <input id="weekly_amount" name="weekly_amount" type="number" min="0" step="0.01" value="{STARTING_WEEKLY_BALANCE:.2f}" required>
          <button type="submit">Reset All Active Campers</button>
        </form>
        <p class="tiny">Use this at the start of each camp week to give each camper the same fresh balance.</p>
      </div>
    </section>

    {admin_markup}

    <section class="card" style="margin-top: 22px;">
      <h2>Campers</h2>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Age</th>
            <th>Card Number</th>
            <th>Balance</th>
            <th>Last Activity</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>{camper_rows}</tbody>
      </table>
    </section>

    <section class="card" style="margin-top: 22px;">
      <h2>Recent Transactions</h2>
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Staff</th>
            <th>Camper</th>
            <th>Card</th>
            <th>Type</th>
            <th>Amount</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>{transaction_rows}</tbody>
      </table>
    </section>
    """

    stocks_content = f"""
    <section class="market-board">
      {ticker_cards}
    </section>

    <section class="grid">
      <div class="card">
        <h2>Buy Stock By Card</h2>
        <form method="post" action="/market/buy">
          <label for="buy_card_number">RFID Card Number</label>
          <input id="buy_card_number" name="card_number" required>
          <label for="buy_symbol">Market</label>
          <select id="buy_symbol" name="symbol" required>
            <option value="PIA">PIA</option>
            <option value="OIL">OIL</option>
            <option value="GOLD">GOLD</option>
            <option value="TECH">TECH</option>
          </select>
          <label for="buy_shares">Shares</label>
          <input id="buy_shares" name="shares" type="number" min="0.01" step="0.01" required>
          <button type="submit">Buy Shares</button>
        </form>
        <p class="tiny">Students buy shares using the same wallet balance already tied to their RFID card.</p>
      </div>

      <div class="card">
        <h2>Sell Stock By Card</h2>
        <form method="post" action="/market/sell">
          <label for="sell_card_number">RFID Card Number</label>
          <input id="sell_card_number" name="card_number" required>
          <label for="sell_symbol">Market</label>
          <select id="sell_symbol" name="symbol" required>
            <option value="PIA">PIA</option>
            <option value="OIL">OIL</option>
            <option value="GOLD">GOLD</option>
            <option value="TECH">TECH</option>
          </select>
          <label for="sell_shares">Shares</label>
          <input id="sell_shares" name="shares" type="number" min="0.01" step="0.01" required>
          <button type="submit">Sell Shares</button>
        </form>
        <p class="tiny">Selling returns the live market value back to the camper's balance instantly.</p>
      </div>
    </section>

    {admin_markup}

    <section class="admin-grid">
      <div class="card">
        <h2>Camp Market Pulse</h2>
        <p><strong>Latest hype note:</strong> {html.escape(latest_event["summary"] if latest_event else "No hype update has been submitted yet.")}</p>
        <p class="mini">Leaders can save the vibe of the day, and the market uses those answers to create a big game-style swing every 12 hours. Between those swings, prices drift a little on their own to keep the board feeling alive.</p>
        <form method="post" action="/market/event">
          <label for="market_summary">Hype Of The Day</label>
          <textarea id="market_summary" name="summary" rows="4" placeholder="Huge color war comeback, wild dining hall buzz, everybody is talking..." required></textarea>
          <label for="energy_level">Camp Buzz (0-100)</label>
          <input id="energy_level" name="energy_level" type="number" min="0" max="100" value="50" required>
          <label for="spirit_level">Overall Hype (0-100)</label>
          <input id="spirit_level" name="spirit_level" type="number" min="0" max="100" value="50" required>
          <label for="weather_score">Weather Chaos (0-100)</label>
          <input id="weather_score" name="weather_score" type="number" min="0" max="100" value="50" required>
          <label for="competition_score">Rivalry Pressure (0-100)</label>
          <input id="competition_score" name="competition_score" type="number" min="0" max="100" value="50" required>
          <button type="submit">Save Hype Inputs</button>
        </form>
        <form method="post" action="/market/refresh" style="margin-top: 12px;">
          <button type="submit">Trigger Big 12-Hour Swing</button>
        </form>
        <p class="tiny">The market is designed to swing big every 12 hours. If refreshed early, the app keeps the current prices and tells staff when the next swing window opens.</p>
      </div>

      <div class="card">
        <h2>Top Student Holdings</h2>
        <table>
          <thead>
            <tr>
              <th>Camper</th>
              <th>Market</th>
              <th>Shares</th>
            </tr>
          </thead>
          <tbody>{top_position_rows}</tbody>
        </table>
      </div>
    </section>

    <section class="card" style="margin-top: 22px;">
      <h2>Market Update History</h2>
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Market</th>
            <th>Price</th>
            <th>Source</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>{market_update_rows}</tbody>
      </table>
    </section>
    """

    students_content = f"""
    <section class="grid">
      <div class="card">
        <h2>Create Student Portal Access</h2>
        <form method="post" action="/students/create">
          <label for="student_camper_id">Student</label>
          <select id="student_camper_id" name="camper_id" required>{camper_options}</select>
          <label for="student_login_name_create">Student Name</label>
          <input id="student_login_name_create" name="login_name" required>
          <label for="student_password_create">Password</label>
          <input id="student_password_create" name="password" type="password" required>
          <button type="submit">Create Student Login</button>
        </form>
        <p class="tiny">Only staff can add student portal access. This keeps card-linked accounts under leader control.</p>
      </div>

      <div class="card">
        <h2>Link Card To Student</h2>
        <form method="post" action="/students/link-card">
          <label for="student_link_camper_id">Student</label>
          <select id="student_link_camper_id" name="camper_id" required>{camper_options}</select>
          <label for="student_link_card_number">Tap or Enter Card Number</label>
          <input id="student_link_card_number" name="card_number" required>
          <button type="submit">Link Card</button>
        </form>
        <p class="tiny">Use this when a leader taps a new card and wants the student's phone portal to stay tied to the right bank account.</p>
      </div>

      <div class="card">
        <h2>Give Promo</h2>
        <form method="post" action="/students/promos/create">
          <label for="promo_camper_id">Student</label>
          <select id="promo_camper_id" name="camper_id" required>{camper_options}</select>
          <label for="promo_title">Promo</label>
          <input id="promo_title" name="title" value="Free Snack" required>
          <label for="promo_details">Details</label>
          <input id="promo_details" name="details" value="Leader reward for one free snack.">
          <button type="submit">Add Promo</button>
        </form>
        <p class="tiny">Leaders can drop rewards into a student's promo wallet so the student sees it right away on their phone.</p>
      </div>
    </section>

    <section class="card" style="margin-top: 22px;">
      <h2>Student Phone Portal</h2>
      <p><strong>Portal link:</strong> <a href="/student">/student</a></p>
      <p class="mini">Students sign in from their phones here to check their wallet, see linked stocks, upload a photo, and swipe promos as used when a leader is holding the phone.</p>
    </section>

    <section class="grid">
      <div class="card">
        <h2>Student Portal Accounts</h2>
        <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Login</th>
              <th>Card</th>
              <th>Photo</th>
              <th>Balance</th>
              <th>Created By</th>
            </tr>
          </thead>
          <tbody>{student_rows}</tbody>
        </table>
        </div>
      </div>

      <div class="card">
        <h2>Student Promos</h2>
        <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Student</th>
              <th>Promo</th>
              <th>Details</th>
              <th>Status</th>
              <th>Given By</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>{promo_table_rows}</tbody>
        </table>
        </div>
      </div>
    </section>

    """

    current_vote_block = (
        f"""
        <div class="card">
          <h2>Current Vote</h2>
          <p><strong>{html.escape(voting_data["session"]["title"])}</strong></p>
          <p class="mini">{html.escape(voting_data["session"]["details"] or "Live camp vote is open now.")}</p>
          <div class="stats" style="margin-top:14px;">
            <div class="stat"><strong>Yes</strong><br>{voting_data["totals"]["yes"]}</div>
            <div class="stat"><strong>No</strong><br>{voting_data["totals"]["no"]}</div>
            <div class="stat"><strong>Voted</strong><br>{voting_data["totals"]["total"]} / {voting_data["totals"]["eligible"]}</div>
            <div class="stat"><strong>Remaining</strong><br>{voting_data["totals"]["remaining"]}</div>
          </div>
          <form method="post" action="/voting/end" style="margin-top:16px;">
            <button type="submit" class="danger-button">End Voting Session</button>
          </form>
          <p class="tiny">Only one live bill runs at a time so the controller and projector stay locked on the same vote.</p>
        </div>
        """
        if voting_data["session"]
        else """
        <div class="card">
          <h2>Start A Bill Vote</h2>
          <form method="post" action="/voting/start">
            <label for="vote_title">Bill Name</label>
            <input id="vote_title" name="title" placeholder="Should the camp dance be moved to tonight?" required>
            <label for="vote_details">Bill Details</label>
            <textarea id="vote_details" name="details" rows="4" placeholder="Short line for the projector and controller screens."></textarea>
            <button type="submit">Start Voting Session</button>
          </form>
          <p class="tiny">Once this starts, the voting PC and projector PC both follow the same live session.</p>
        </div>
        """
    )

    recent_vote_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row["camper_name"])}</td>
          <td>{html.escape(row["vote_value"])}</td>
          <td>{html.escape(row["cast_by"])}</td>
          <td>{html.escape(row["created_at"])}</td>
        </tr>
        """
        for row in voting_data["recent_votes"]
    ) or '<tr><td colspan="4">No votes yet.</td></tr>'

    voting_content = f"""
    <section class="voting-shell">
      <section class="voting-launchers">
        <a class="voting-link" href="/voting/controller">
          <strong>Voting PC</strong>
          Scan a camper card, see the name pop up, and tap Yes or No.
        </a>
        <a class="voting-link" href="/voting/projector" target="_blank" rel="noreferrer">
          <strong>Projector PC</strong>
          Open the live board with giant Yes and No sides for the room.
        </a>
      </section>

      <section class="grid">
        {current_vote_block}
        <div class="card">
          <h2>How This Works</h2>
          <p class="mini">1. Start one bill.</p>
          <p class="mini">2. Open <strong>Voting PC</strong> on the laptop that scans cards.</p>
          <p class="mini">3. Open <strong>Projector PC</strong> on the display computer.</p>
          <p class="mini">4. Each camper scans once and the projector updates live from the same vote table.</p>
        </div>
      </section>

      <section class="grid">
        <div class="card">
          <h2>Recent Votes</h2>
          <table>
            <thead>
              <tr>
                <th>Camper</th>
                <th>Vote</th>
                <th>Cast By</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>{recent_vote_rows}</tbody>
          </table>
        </div>

        <div class="card">
          <h2>Recent Bill Sessions</h2>
          <div class="session-list">{session_rows}</div>
        </div>
      </section>
    </section>
    """

    content = f"""
    <section class="hero">
      <div class="hero-top">
        <div class="logo-shell">
          <img src="/assets/pia-logo.jpeg" alt="Camp logo">
        </div>
        <div>
          <span class="pill">RFID Camp Wallet</span>
          <span class="hero-tagline">Weekly Spending Dashboard</span>
          <h1>Camp Card System</h1>
          <p class="sub">Use the tabs below to keep the camper bank tools separate from the camp stock market, the student phone portal, and live camp voting. Campers start with {money(STARTING_WEEKLY_BALANCE)} each, and the market swings hard every 12 hours with small live moves in between.</p>
        </div>
      </div>
    </section>

    <div class="tabbar">
      <a class="tablink {'active' if active_tab == 'bank' else ''}" href="/?tab=bank">Bank</a>
      <a class="tablink {'active' if active_tab == 'stocks' else ''}" href="/?tab=stocks">Stocks</a>
      <a class="tablink {'active' if active_tab == 'students' else ''}" href="/?tab=students">Students</a>
      <a class="tablink {'active' if active_tab == 'voting' else ''}" href="/?tab=voting">Voting</a>
    </div>

    <section class="stats">
      <div class="stat"><strong>Active Campers</strong><br>{camper_count}</div>
      <div class="stat"><strong>Total Stored Balance</strong><br>{money(total_balance)}</div>
      <div class="stat"><strong>Total Transactions</strong><br>{transaction_count}</div>
      <div class="stat"><strong>Student Logins</strong><br>{student_count}</div>
      <div class="stat"><strong>Open Promos</strong><br>{available_promo_count}</div>
      <div class="stat"><strong>Default Weekly Amount</strong><br>{money(STARTING_WEEKLY_BALANCE)}</div>
    </section>

    {bank_content if active_tab == 'bank' else stocks_content if active_tab == 'stocks' else students_content if active_tab == 'students' else voting_content}
    """
    return page_template(content, user=user, message=message, error=error, action=action)


def render_lookup(user, card_number):
    conn = get_db()
    camper = conn.execute(
        "SELECT * FROM campers WHERE card_number = ? AND active = 1",
        (card_number,),
    ).fetchone()
    transactions = []
    if camper:
        transactions = conn.execute(
            """
            SELECT *
            FROM transactions
            WHERE camper_id = ?
            ORDER BY id DESC
            LIMIT 10
            """,
            (camper["id"],),
        ).fetchall()
    conn.close()

    if not camper:
        return page_template(
            f"""
            <section class="card">
              <h2>Card Lookup</h2>
              <p>No active camper found for card number <strong>{html.escape(card_number)}</strong>.</p>
              <p><a href="/">Back to dashboard</a></p>
            </section>
            """,
            user=user,
            error="Card number not found.",
        )

    transaction_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row["created_at"])}</td>
          <td>{html.escape(row["actor_username"] or "Unknown")}</td>
          <td>{html.escape(row["kind"])}</td>
          <td>{money(row["amount"])}</td>
          <td>{html.escape(row["note"])}</td>
        </tr>
        """
        for row in transactions
    ) or '<tr><td colspan="5">No transactions yet.</td></tr>'

    content = f"""
    <section class="card">
      <h2>Camper Found</h2>
      <p><strong>Name:</strong> {html.escape(camper["name"])}</p>
      <p><strong>Age:</strong> {camper["age"]}</p>
      <p><strong>RFID Card:</strong> {html.escape(camper["card_number"])}</p>
      <p><strong>Current Balance:</strong> {money(camper["balance"])}</p>
      <p><a href="/">Back to dashboard</a></p>
    </section>
    <section class="card" style="margin-top: 22px;">
      <h2>Recent Activity</h2>
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Staff</th>
            <th>Type</th>
            <th>Amount</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>{transaction_rows}</tbody>
      </table>
    </section>
    """
    return page_template(content, user=user, message="Card lookup successful.")


def get_camper_by_card(conn, card_number):
    return conn.execute(
        "SELECT * FROM campers WHERE card_number = ? AND active = 1",
        (card_number,),
    ).fetchone()


def get_camper_by_name(conn, name):
    return conn.execute(
        """
        SELECT *
        FROM campers
        WHERE LOWER(name) = LOWER(?) AND active = 1
        ORDER BY id DESC
        LIMIT 1
        """,
        (name,),
    ).fetchone()


def get_student_user_by_camper(conn, camper_id):
    return conn.execute(
        """
        SELECT student_users.*, campers.name, campers.card_number, campers.balance
        FROM student_users
        JOIN campers ON campers.id = student_users.camper_id
        WHERE student_users.camper_id = ?
        """,
        (camper_id,),
    ).fetchone()


def get_student_user_by_login(conn, login_name):
    return conn.execute(
        """
        SELECT student_users.*, campers.name, campers.card_number, campers.balance, campers.active
        FROM student_users
        JOIN campers ON campers.id = student_users.camper_id
        WHERE LOWER(student_users.login_name) = LOWER(?)
        """,
        (login_name,),
    ).fetchone()


def student_photo_url(student):
    photo_path = (student["photo_path"] or "").strip()
    if not photo_path:
        return ""
    return "/" + photo_path.replace(os.sep, "/").lstrip("/")


def portfolio_summary(conn, camper_id):
    rows = conn.execute(
        """
        SELECT market_assets.symbol AS symbol, market_positions.asset_symbol, market_positions.shares, market_assets.name,
               market_assets.current_price, market_assets.previous_price, market_assets.updated_at
        FROM market_positions
        JOIN market_assets ON market_assets.symbol = market_positions.asset_symbol
        WHERE market_positions.camper_id = ? AND market_positions.shares > 0
        ORDER BY market_positions.asset_symbol
        """,
        (camper_id,),
    ).fetchall()
    holdings = []
    total_value = 0.0
    for row in rows:
        live_price = live_market_price(row)
        market_value = round(live_price * row["shares"], 2)
        total_value += market_value
        holdings.append(
            {
                "symbol": row["asset_symbol"],
                "name": row["name"],
                "shares": row["shares"],
                "live_price": live_price,
                "market_value": market_value,
                "change": live_price - row["previous_price"],
            }
        )
    return holdings, round(total_value, 2)


def get_student_promos(conn, camper_id, status=None):
    if status:
        return conn.execute(
            """
            SELECT *
            FROM student_promos
            WHERE camper_id = ? AND status = ?
            ORDER BY id DESC
            """,
            (camper_id, status),
        ).fetchall()
    return conn.execute(
        """
        SELECT *
        FROM student_promos
        WHERE camper_id = ?
        ORDER BY id DESC
        """,
        (camper_id,),
    ).fetchall()


def get_active_voting_session(conn):
    return conn.execute(
        """
        SELECT *
        FROM voting_sessions
        WHERE status = 'ACTIVE'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()


def get_recent_voting_sessions(conn, limit=8):
    return conn.execute(
        """
        SELECT voting_sessions.*,
               COALESCE(SUM(CASE WHEN voting_votes.vote_value = 'YES' THEN 1 ELSE 0 END), 0) AS yes_votes,
               COALESCE(SUM(CASE WHEN voting_votes.vote_value = 'NO' THEN 1 ELSE 0 END), 0) AS no_votes
        FROM voting_sessions
        LEFT JOIN voting_votes ON voting_votes.session_id = voting_sessions.id
        GROUP BY voting_sessions.id
        ORDER BY voting_sessions.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def get_voting_votes(conn, session_id, limit=None):
    sql = """
        SELECT *
        FROM voting_votes
        WHERE session_id = ?
        ORDER BY id DESC
    """
    params = [session_id]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return conn.execute(sql, params).fetchall()


def voting_payload(conn, session):
    eligible = conn.execute("SELECT COUNT(*) AS count FROM campers WHERE active = 1").fetchone()["count"]
    if not session:
        return {
            "session": None,
            "totals": {"yes": 0, "no": 0, "total": 0, "eligible": eligible, "remaining": eligible},
            "recent_votes": [],
            "yes_votes": [],
            "no_votes": [],
        }
    votes = get_voting_votes(conn, session["id"])
    yes_votes = [dict(row) for row in votes if row["vote_value"] == "YES"]
    no_votes = [dict(row) for row in votes if row["vote_value"] == "NO"]
    total = len(votes)
    return {
        "session": dict(session),
        "totals": {
            "yes": len(yes_votes),
            "no": len(no_votes),
            "total": total,
            "eligible": eligible,
            "remaining": max(eligible - total, 0),
        },
        "recent_votes": [dict(row) for row in votes[:12]],
        "yes_votes": yes_votes[:24],
        "no_votes": no_votes[:24],
    }


def render_vote_name_cards(votes, side_class):
    return "".join(
        f'<div class="vote-name-card {side_class}"><strong>{html.escape(row["camper_name"])}</strong><span>{html.escape(row["created_at"])}</span></div>'
        for row in votes
    ) or '<div class="vote-name-card empty"><strong>No votes yet</strong><span>Waiting for scans</span></div>'


def safe_file_ext(filename, content_type):
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return ext
    guessed = mimetypes.guess_extension(content_type or "") or ".jpg"
    return ".jpg" if guessed == ".jpe" else guessed


def clamp_float(value, minimum, maximum, default):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def render_student_login(message="", error=""):
    content = f"""
    <div class="login-shell">
      <section class="hero student-hero">
        <div class="hero-top">
          <div class="logo-shell">
            <img src="/assets/pia-logo.jpeg" alt="Camp logo">
          </div>
          <div>
            <span class="pill">Student Portal</span>
            <span class="hero-tagline">Phone-Friendly Wallet View</span>
            <h1>My Camp Wallet</h1>
            <p class="sub">Students can log in to check their balance, view stocks tied to their card, see promos, and upload a profile photo.</p>
          </div>
        </div>
      </section>
      {f'<div class="notice success">{html.escape(message)}</div>' if message else ''}
      {f'<div class="notice error">{html.escape(error)}</div>' if error else ''}
      <section class="card" style="margin-top: 22px;">
        <h2>Student Login</h2>
        <form method="post" action="/student/login">
          <label for="student_login_name">Student Name</label>
          <input id="student_login_name" name="login_name" required>
          <label for="student_password">Password</label>
          <input id="student_password" name="password" type="password" required>
          <button type="submit">Open My Account</button>
        </form>
        <p class="tiny">A leader or under leader must create the student login first and link it to the student's card.</p>
      </section>
    </div>
    """
    return page_template(content, message=message, error=error)


def render_student_home(student, message="", error="", action=""):
    conn = get_db()
    refreshed_student = conn.execute(
        """
        SELECT student_users.*, campers.name, campers.card_number, campers.balance, campers.active
        FROM student_users
        JOIN campers ON campers.id = student_users.camper_id
        WHERE student_users.id = ?
        """,
        (student["id"],),
    ).fetchone()
    transactions = conn.execute(
        """
        SELECT created_at, actor_username, kind, amount, note
        FROM transactions
        WHERE camper_id = ?
        ORDER BY id DESC
        LIMIT 12
        """,
        (student["camper_id"],),
    ).fetchall()
    promos = get_student_promos(conn, student["camper_id"])
    holdings, total_value = portfolio_summary(conn, student["camper_id"])
    conn.close()

    banner_title = f"{refreshed_student['name'].upper()}'S WALLET"
    banner_subtitle = "Track balance, holdings, and rewards."
    banner_theme = refreshed_student["banner_theme"] if refreshed_student["banner_theme"] in {"violet", "ocean", "sunset", "emerald"} else "violet"
    photo_style = f'object-position:{refreshed_student["photo_x"]:.1f}% {refreshed_student["photo_y"]:.1f}%; transform:scale(1);'
    photo_markup = (
        f'<img class="student-photo" src="{html.escape(student_photo_url(refreshed_student))}" alt="{html.escape(refreshed_student["name"])}" style="{photo_style}">'
        if refreshed_student["photo_path"]
        else f'<div class="student-photo photo-placeholder">{html.escape(refreshed_student["name"][:2].upper())}</div>'
    )
    holding_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row["symbol"])}</td>
          <td>{number(row["shares"])}</td>
          <td>{money(row["live_price"])}</td>
          <td>{money(row["market_value"])}</td>
        </tr>
        """
        for row in holdings
    ) or '<tr><td colspan="4">No stocks linked yet.</td></tr>'
    promo_items = "".join(
        f"""
        <div class="list-item soft-card">
          <div class="promo-chip">{html.escape(row["status"])}</div>
          <h3 style="margin:10px 0 6px;">{html.escape(row["title"])}</h3>
          <div class="mini">{html.escape(row["details"] or "Camp promo")}</div>
          <div class="mini"><strong>Given by:</strong> {html.escape(row["granted_by"])}</div>
          <div class="mini"><strong>Added:</strong> {html.escape(row["created_at"])}</div>
          {
            f'''
            <form method="post" action="/student/promo/use" class="promo-swipe" data-swipe-form>
              <input type="hidden" name="promo_id" value="{row["id"]}">
              <div class="swipe-track">
                <label for="promo_swipe_{row["id"]}">Swipe to use</label>
                <input id="promo_swipe_{row["id"]}" name="swipe_value" type="range" min="0" max="100" value="0" data-swipe-input>
                <div class="swipe-status" data-swipe-status>Slide all the way so a leader can mark this promo as used.</div>
              </div>
              <button type="submit" class="locked-button" data-swipe-button disabled>Use Promo</button>
            </form>
            ''' if row["status"] == "AVAILABLE" else f'<div class="mini"><strong>Used:</strong> {html.escape(row["redeemed_at"] or "Redeemed")}</div>'
          }
        </div>
        """
        for row in promos
    ) or '<div class="list-item">No promos have been added yet.</div>'
    transaction_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row["created_at"])}</td>
          <td>{html.escape(row["kind"])}</td>
          <td>{money(row["amount"])}</td>
          <td>{html.escape(row["note"])}</td>
        </tr>
        """
        for row in transactions
    ) or '<tr><td colspan="4">No activity yet.</td></tr>'

    content = f"""
    <section class="student-shell">
      <aside class="student-sidebar">
        <div style="display:grid; gap:14px; justify-items:start;">
          {photo_markup}
          <div>
            <span class="pill" style="background:rgba(255,255,255,0.14);">Student</span>
            <h2 style="margin:12px 0 4px; color:#fff;">{html.escape(refreshed_student["name"])}</h2>
            <div class="mini" style="color:rgba(255,255,255,0.78);"><strong>Login:</strong> {html.escape(refreshed_student["login_name"])}</div>
            <div class="mini" style="color:rgba(255,255,255,0.78);"><strong>Card:</strong> {html.escape(refreshed_student["card_number"])}</div>
          </div>
        </div>
        <div class="student-rail">
          <div class="rail-item">Wallet Balance: {money(refreshed_student["balance"])}</div>
          <div class="rail-item">Stock Value: {money(total_value)}</div>
          <div class="rail-item">Open Promos: {len([row for row in promos if row["status"] == "AVAILABLE"])}</div>
        </div>
        <form method="post" action="/student/logout" class="logout-form" style="margin-top:18px;">
          <button type="submit">Log Out</button>
        </form>
      </aside>

      <div class="student-main">
        <section class="student-banner banner-{html.escape(banner_theme)}">
          <div style="display:flex; justify-content:space-between; gap:16px; align-items:flex-start;">
            <div>
          <span class="pill" style="background:rgba(255,255,255,0.16);">My Portal</span>
          <h1 style="margin:14px 0 8px; max-width:520px;">{html.escape(banner_title)}</h1>
          <p class="sub" style="max-width:560px;">{html.escape(banner_subtitle)}</p>
          <div class="mini" style="color:rgba(255,255,255,0.86); margin-top:14px;">Leaders handle stock buying from the leader dashboard. Students can track everything here and use rewards from their phone.</div>
            </div>
            <div class="settings-wrap" data-settings-wrap data-open="false">
              <button type="button" class="settings-toggle" title="Student settings" data-settings-button>⚙</button>
              <div class="settings-card">
                <h3>Profile & Banner</h3>
                <form method="post" action="/student/photo" enctype="multipart/form-data">
                  <div class="settings-section">
                    <p class="settings-section-title">Profile Photo</p>
                    <p class="settings-help">Pick a new photo for the student card on the left.</p>
                    <label for="student_photo">Choose Photo</label>
                    <input id="student_photo" name="photo" type="file" accept="image/*" required>
                    <button type="submit">Save Photo</button>
                  </div>
                </form>
                <form method="post" action="/student/banner" style="margin-top:14px;">
                  <div class="settings-section">
                    <p class="settings-section-title">Banner Theme</p>
                    <p class="settings-help">Switch the top banner color style for this student portal.</p>
                    <label for="banner_theme">Theme</label>
                    <select id="banner_theme" name="banner_theme" required>
                      <option value="violet" {'selected' if banner_theme == 'violet' else ''}>Violet</option>
                      <option value="ocean" {'selected' if banner_theme == 'ocean' else ''}>Ocean</option>
                      <option value="sunset" {'selected' if banner_theme == 'sunset' else ''}>Sunset</option>
                      <option value="emerald" {'selected' if banner_theme == 'emerald' else ''}>Emerald</option>
                    </select>
                    <button type="submit">Save Theme</button>
                  </div>
                </form>
              </div>
            </div>
          </div>
        </section>

        <section class="student-kpis">
          <div class="student-kpi"><strong>Bank Balance</strong><span>{money(refreshed_student["balance"])}</span></div>
          <div class="student-kpi"><strong>Stock Value</strong><span>{money(total_value)}</span></div>
          <div class="student-kpi"><strong>Total Value</strong><span>{money(refreshed_student["balance"] + total_value)}</span></div>
          <div class="student-kpi"><strong>Open Promos</strong><span>{len([row for row in promos if row["status"] == "AVAILABLE"])}</span></div>
        </section>

        <section class="grid">
          <div class="card soft-card">
            <h2>My Stocks</h2>
            <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Market</th>
                  <th>Shares</th>
                  <th>Live Price</th>
                  <th>Value</th>
                </tr>
              </thead>
              <tbody>{holding_rows}</tbody>
            </table>
            </div>
          </div>

          <div class="card soft-card">
            <h2>My Promos</h2>
            <div class="list-stack">{promo_items}</div>
          </div>
        </section>

        <section class="card soft-card" style="margin-top: 0;">
        <h2>Recent Activity</h2>
        <div class="student-note">Latest activity tied to your card and account.</div>
        <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Type</th>
              <th>Amount</th>
              <th>Note</th>
            </tr>
          </thead>
          <tbody>{transaction_rows}</tbody>
        </table>
        </div>
        </section>
      </div>
    </section>
    <script>
      (function() {{
        var settingsWrap = document.querySelector('[data-settings-wrap]');
        if (settingsWrap) {{
          var settingsButton = settingsWrap.querySelector('[data-settings-button]');
          settingsButton.addEventListener('click', function(event) {{
            event.stopPropagation();
            settingsWrap.setAttribute('data-open', settingsWrap.getAttribute('data-open') === 'true' ? 'false' : 'true');
          }});
          settingsWrap.addEventListener('click', function(event) {{
            event.stopPropagation();
          }});
          document.addEventListener('click', function() {{
            settingsWrap.setAttribute('data-open', 'false');
          }});
        }}
        document.querySelectorAll('[data-swipe-form]').forEach(function(swipeForm) {{
          var slider = swipeForm.querySelector('[data-swipe-input]');
          var button = swipeForm.querySelector('[data-swipe-button]');
          var status = swipeForm.querySelector('[data-swipe-status]');
          var sync = function() {{
            var unlocked = Number(slider.value) >= 100;
            button.disabled = !unlocked;
            button.className = unlocked ? '' : 'locked-button';
            status.textContent = unlocked ? 'Release the button to use this promo now.' : 'Slide all the way so a leader can mark this promo as used.';
          }};
          slider.addEventListener('input', sync);
          swipeForm.addEventListener('submit', function(event) {{
            if (Number(slider.value) < 100) {{
              event.preventDefault();
              sync();
            }}
          }});
          sync();
        }});
      }})();
    </script>
    """
    return page_template(content, message=message, error=error, action=action)


def render_voting_controller(user, message="", error="", action=""):
    conn = get_db()
    session = get_active_voting_session(conn)
    voting_data = voting_payload(conn, session)
    conn.close()
    current_title = html.escape(voting_data["session"]["title"]) if voting_data["session"] else "No active bill"
    current_details = html.escape(voting_data["session"]["details"] or "Start a bill from the Voting tab first.") if voting_data["session"] else "Open the Voting tab on the leader dashboard and start a live vote."
    recent_votes = "".join(
        f"""
        <tr>
          <td>{html.escape(row["camper_name"])}</td>
          <td>{html.escape(row["vote_value"])}</td>
          <td>{html.escape(row["cast_by"])}</td>
          <td>{html.escape(row["created_at"])}</td>
        </tr>
        """
        for row in voting_data["recent_votes"]
    ) or '<tr><td colspan="4">No votes have been scanned yet.</td></tr>'
    content = f"""
    <section class="hero">
      <div class="hero-top" style="grid-template-columns:minmax(120px,190px) 1fr;">
        <div class="logo-shell">
          <img src="/assets/pia-logo.jpeg" alt="Camp logo">
        </div>
        <div>
          <span class="pill">Voting PC</span>
          <span class="hero-tagline">Scan Card Then Tap Yes Or No</span>
          <h1>Live Camp Voting</h1>
          <p class="sub">This is the control screen. Scan or type one camper card, confirm the camper name, and hit the side they chose.</p>
        </div>
      </div>
    </section>

    <section class="voting-board">
      <div class="card">
        <h2>Vote Scanner</h2>
        <div class="vote-status-panel">
          <div class="vote-status-pill">Current Bill</div>
          <div class="vote-status-name">{current_title}</div>
          <div class="mini">{current_details}</div>
        </div>
        <form method="post" action="/voting/vote" style="margin-top:16px;">
          <label for="vote_card_number">RFID Card Number</label>
          <input id="vote_card_number" name="card_number" placeholder="Tap or scan camper card" {'disabled' if not voting_data["session"] else ''} autofocus required>
          <div class="vote-buttons">
            <button type="submit" name="vote_value" value="YES" class="yes-button" {'disabled' if not voting_data["session"] else ''}>Vote Yes</button>
            <button type="submit" name="vote_value" value="NO" class="no-button" {'disabled' if not voting_data["session"] else ''}>Vote No</button>
          </div>
        </form>
        <p class="tiny">Each camper is tied to one active card. If the same camper scans again during the same bill, their vote updates to the new side.</p>
      </div>

      <div class="card">
        <h2>Live Totals</h2>
        <div class="stats" style="margin-top:0;">
          <div class="stat"><strong>Yes</strong><br>{voting_data["totals"]["yes"]}</div>
          <div class="stat"><strong>No</strong><br>{voting_data["totals"]["no"]}</div>
          <div class="stat"><strong>Total Voted</strong><br>{voting_data["totals"]["total"]}</div>
          <div class="stat"><strong>Remaining</strong><br>{voting_data["totals"]["remaining"]}</div>
        </div>
        <div class="table-wrap" style="margin-top:18px;">
          <table>
            <thead>
              <tr>
                <th>Camper</th>
                <th>Vote</th>
                <th>Cast By</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>{recent_votes}</tbody>
          </table>
        </div>
        <div class="inline-actions" style="margin-top:16px;">
          <a class="voting-link" href="/voting/projector" target="_blank" rel="noreferrer" style="flex:1 1 220px;">Open projector board in a new window</a>
        </div>
      </div>
    </section>
    """
    return page_template(content, user=user, message=message, error=error, action=action)


def render_voting_projector(user):
    conn = get_db()
    session = get_active_voting_session(conn)
    payload = voting_payload(conn, session)
    conn.close()
    yes_cards = render_vote_name_cards(payload["yes_votes"], "yes")
    no_cards = render_vote_name_cards(payload["no_votes"], "no")
    title = html.escape(payload["session"]["title"]) if payload["session"] else "Waiting for the next camp bill"
    details = html.escape(payload["session"]["details"] or "Voting is open now.") if payload["session"] else "Start a live session from the Voting tab or the voting PC."
    refreshed_at = now()
    script = f"""
    <script>
      (function() {{
        var titleNode = document.querySelector('[data-vote-title]');
        var detailsNode = document.querySelector('[data-vote-details]');
        var yesNode = document.querySelector('[data-vote-yes]');
        var noNode = document.querySelector('[data-vote-no]');
        var yesList = document.querySelector('[data-vote-yes-list]');
        var noList = document.querySelector('[data-vote-no-list]');
        var totalNode = document.querySelector('[data-vote-total]');
        var remainNode = document.querySelector('[data-vote-remaining]');
        var refreshNode = document.querySelector('[data-vote-refresh]');
        var escapeHtml = function(value) {{
          return String(value || '').replace(/[&<>\"']/g, function(ch) {{
            return {{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}}[ch];
          }});
        }};
        var renderCards = function(items) {{
          if (!items.length) {{
            return '<div class="vote-name-card empty"><strong>No votes yet</strong><span>Waiting for scans</span></div>';
          }}
          return items.map(function(item) {{
            return '<div class="vote-name-card"><strong>' + escapeHtml(item.camper_name) + '</strong><span>' + escapeHtml(item.created_at) + '</span></div>';
          }}).join('');
        }};
        var refresh = function() {{
          fetch('/voting/live')
            .then(function(response) {{ return response.json(); }})
            .then(function(data) {{
              titleNode.textContent = data.session ? data.session.title : 'Waiting for the next camp bill';
              detailsNode.textContent = data.session ? (data.session.details || 'Voting is open now.') : 'Start a live session from the Voting tab or the voting PC.';
              yesNode.textContent = data.totals.yes;
              noNode.textContent = data.totals.no;
              totalNode.textContent = data.totals.total + ' / ' + data.totals.eligible;
              remainNode.textContent = data.totals.remaining;
              yesList.innerHTML = renderCards(data.yes_votes || []);
              noList.innerHTML = renderCards(data.no_votes || []);
              refreshNode.textContent = 'Last sync: ' + new Date().toLocaleTimeString();
            }})
            .catch(function() {{}});
        }};
        setInterval(refresh, 3000);
      }})();
    </script>
    """
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Camp Voting Projector</title>
  <style>{page_template("", user=user).split("<style>", 1)[1].split("</style>", 1)[0]}</style>
</head>
<body>
  <div class="projector-shell">
    <section class="projector-hero">
      <span class="pill">Projector PC</span>
      <h1 data-vote-title>{title}</h1>
      <p class="sub" data-vote-details>{details}</p>
      <div class="projector-meta">
        <div class="projector-meta-card"><strong>Total Voted</strong><div data-vote-total>{payload["totals"]["total"]} / {payload["totals"]["eligible"]}</div></div>
        <div class="projector-meta-card"><strong>Still Waiting</strong><div data-vote-remaining>{payload["totals"]["remaining"]}</div></div>
        <div class="projector-meta-card"><strong>Live Sync</strong><div data-vote-refresh>Last sync: {refreshed_at}</div></div>
      </div>
    </section>

    <section class="projector-board">
      <div class="vote-side yes">
        <h2>Yes</h2>
        <div class="vote-big-number" data-vote-yes>{payload["totals"]["yes"]}</div>
        <div class="vote-name-stack" data-vote-yes-list>{yes_cards}</div>
      </div>
      <div class="vote-side no">
        <h2>No</h2>
        <div class="vote-big-number" data-vote-no>{payload["totals"]["no"]}</div>
        <div class="vote-name-stack" data-vote-no-list>{no_cards}</div>
      </div>
    </section>
  </div>
  {script}
</body>
</html>"""


def make_card_available(conn, card_number):
    existing = conn.execute(
        "SELECT * FROM campers WHERE card_number = ? ORDER BY id DESC LIMIT 1",
        (card_number,),
    ).fetchone()
    if not existing:
        return True, ""
    if existing["active"]:
        return False, "That RFID card number is already assigned to another active camper."
    conn.execute(
        "UPDATE campers SET card_number = ? WHERE id = ?",
        (archived_card_number(card_number, existing["id"]), existing["id"]),
    )
    return True, ""


def insert_transaction(conn, camper_id, kind, amount, note, actor_username):
    conn.execute(
        """
        INSERT INTO transactions (camper_id, kind, amount, note, created_at, actor_username)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (camper_id, kind, amount, note, now(), actor_username),
    )


def parse_score(value, label):
    try:
        number_value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a whole number between 0 and 100.")
    if number_value < 0 or number_value > 100:
        raise ValueError(f"{label} must be between 0 and 100.")
    return number_value


def get_market_asset(conn, symbol):
    return conn.execute(
        "SELECT * FROM market_assets WHERE symbol = ?",
        (symbol.upper(),),
    ).fetchone()


def get_position(conn, camper_id, symbol):
    return conn.execute(
        """
        SELECT *
        FROM market_positions
        WHERE camper_id = ? AND asset_symbol = ?
        """,
        (camper_id, symbol.upper()),
    ).fetchone()


def upsert_position(conn, camper_id, symbol, shares):
    existing = get_position(conn, camper_id, symbol)
    timestamp = now()
    if existing:
        conn.execute(
            "UPDATE market_positions SET shares = ?, updated_at = ? WHERE id = ?",
            (shares, timestamp, existing["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO market_positions (camper_id, asset_symbol, shares, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (camper_id, symbol.upper(), shares, timestamp, timestamp),
        )


def get_latest_market_event(conn):
    return conn.execute(
        "SELECT * FROM market_events ORDER BY id DESC LIMIT 1"
    ).fetchone()


def snapshot_reason(event):
    if not event:
        return "No hype submitted yet."
    return event["summary"][:140]


def stable_wave(symbol, anchor_text, bucket):
    digest = hashlib.sha256(f"{symbol}|{anchor_text}|{bucket}".encode("utf-8")).hexdigest()
    n1 = int(digest[:8], 16) / 0xFFFFFFFF
    n2 = int(digest[8:16], 16) / 0xFFFFFFFF
    return (n1 * 2.0 - 1.0) * 0.65 + (n2 * 2.0 - 1.0) * 0.35


def live_market_price(asset, current_time=None):
    current_time = current_time or datetime.now()
    anchor_time = datetime.strptime(asset["updated_at"], "%Y-%m-%d %H:%M:%S")
    elapsed_minutes = max(0, (current_time - anchor_time).total_seconds() / 60.0)
    bucket = int(elapsed_minutes // LIVE_MARKET_BUCKET_MINUTES)
    if bucket <= 0:
        return round(asset["current_price"], 2)
    phase = min(1.0, elapsed_minutes / (MARKET_REFRESH_HOURS * 60.0))
    wave = stable_wave(asset["symbol"], asset["updated_at"], bucket)
    drift_pct = max(
        -LIVE_MARKET_MAX_SWING_PCT,
        min(LIVE_MARKET_MAX_SWING_PCT, wave * LIVE_MARKET_MAX_SWING_PCT * (0.45 + phase * 0.55)),
    )
    return round(max(5.0, asset["current_price"] * (1 + drift_pct / 100.0)), 2)


def get_market_history(conn, symbol, limit=10):
    rows = conn.execute(
        """
        SELECT price, created_at, source
        FROM market_snapshots
        WHERE asset_symbol = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (symbol, limit),
    ).fetchall()
    return list(reversed(rows))


def market_chart_svg(points, stroke):
    if len(points) < 2:
        return ""
    prices = [point["price"] for point in points]
    min_price = min(prices)
    max_price = max(prices)
    spread = max(max_price - min_price, 1)
    width = 220
    height = 84
    coords = []
    for index, point in enumerate(points):
        x = 8 + (index / max(len(points) - 1, 1)) * (width - 16)
        normalized = (point["price"] - min_price) / spread
        y = height - 10 - normalized * (height - 20)
        coords.append((round(x, 2), round(y, 2)))
    line = " ".join(f"{x},{y}" for x, y in coords)
    area = f"8,{height - 8} " + " ".join(f"{x},{y}" for x, y in coords) + f" {coords[-1][0]},{height - 8}"
    return f"""
    <svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" aria-hidden="true">
      <polygon points="{area}" fill="{stroke}22"></polygon>
      <polyline points="{line}" fill="none" stroke="{stroke}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></polyline>
    </svg>
    """


def next_refresh_time(conn):
    last_snapshot = conn.execute(
        """
        SELECT created_at
        FROM market_snapshots
        WHERE source != 'seed'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if not last_snapshot:
        return None
    last_time = datetime.strptime(last_snapshot["created_at"], "%Y-%m-%d %H:%M:%S")
    return last_time + timedelta(hours=MARKET_REFRESH_HOURS)


def build_rule_based_market(event, assets):
    event = event or {
        "summary": "Quiet camp day",
        "energy_level": 50,
        "spirit_level": 50,
        "weather_score": 50,
        "competition_score": 50,
    }
    buzz = event["energy_level"] - 50
    hype = event["spirit_level"] - 50
    weather = event["weather_score"] - 50
    rivalry = event["competition_score"] - 50
    summary = event["summary"]
    summary_boost = ((sum(ord(ch) for ch in summary) % 19) - 9) / 10.0
    influences = {
        "PIA": 0.50 * hype + 0.36 * rivalry + 0.14 * buzz + summary_boost,
        "OIL": 0.62 * buzz - 0.18 * weather + 0.20 * rivalry + summary_boost * 1.3,
        "GOLD": -0.25 * buzz + 0.54 * weather + 0.28 * hype - 0.08 * rivalry,
        "TECH": 0.70 * buzz + 0.22 * hype - 0.14 * weather + summary_boost * 1.1,
    }
    updates = []
    for asset in assets:
        swing_seed = stable_wave(asset["symbol"], summary, len(summary))
        delta_pct = influences.get(asset["symbol"], 0.0) + swing_seed * 8.5
        delta_pct = max(-32.0, min(34.0, delta_pct))
        new_price = round(max(5.0, asset["current_price"] * (1 + (delta_pct / 100.0))), 2)
        direction = "jumped" if delta_pct > 0 else "slid"
        reason = f"{asset['symbol']} {direction} after '{summary[:90]}' with a {delta_pct:+.1f}% swing."
        updates.append(
            {
                "symbol": asset["symbol"],
                "price": new_price,
                "reason": reason,
                "source": "major",
            }
        )
    return updates


def apply_market_updates(conn, updates):
    timestamp = now()
    for update in updates:
        current = get_market_asset(conn, update["symbol"])
        conn.execute(
            """
            UPDATE market_assets
            SET previous_price = ?, current_price = ?, updated_at = ?, last_reason = ?
            WHERE symbol = ?
            """,
            (current["current_price"], update["price"], timestamp, update["reason"], update["symbol"]),
        )
        conn.execute(
            """
            INSERT INTO market_snapshots (asset_symbol, price, reason, source, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (update["symbol"], update["price"], update["reason"], update["source"], timestamp),
        )


def handle_market_event(user, data):
    summary = data.get("summary", "")
    if not summary:
        return render_home(user, error="A hype of the day summary is required before the market can react.", tab="stocks")
    try:
        energy_level = parse_score(data.get("energy_level"), "Camp energy")
        spirit_level = parse_score(data.get("spirit_level"), "Camp spirit")
        weather_score = parse_score(data.get("weather_score"), "Weather score")
        competition_score = parse_score(data.get("competition_score"), "Competition pressure")
    except ValueError as exc:
        return render_home(user, error=str(exc), tab="stocks")
    conn = get_db()
    conn.execute(
        """
        INSERT INTO market_events (summary, energy_level, spirit_level, weather_score, competition_score, submitted_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (summary, energy_level, spirit_level, weather_score, competition_score, user["username"], now()),
    )
    log_action(conn, user, "market_event", f"Saved hype inputs: {summary[:80]}")
    conn.commit()
    conn.close()
    return render_home(user, message="Saved today's hype inputs.", action="market_event", tab="stocks")


def handle_market_refresh(user):
    conn = get_db()
    next_time = next_refresh_time(conn)
    if next_time and datetime.now() < next_time:
        wait_text = next_time.strftime("%Y-%m-%d %H:%M:%S")
        conn.close()
        return render_home(user, error=f"The next big market swing window opens at {wait_text}.", tab="stocks")
    assets = conn.execute("SELECT * FROM market_assets ORDER BY symbol").fetchall()
    event = get_latest_market_event(conn)
    updates = build_rule_based_market(event, assets)
    apply_market_updates(conn, updates)
    log_action(conn, user, "market_refresh", f"Triggered a major market swing using the hype inputs")
    conn.commit()
    conn.close()
    return render_home(user, message="Market prices exploded into a new 12-hour swing.", action="market_refresh", tab="stocks")


def handle_trade(user, data, side):
    card_number = data.get("card_number", "")
    symbol = data.get("symbol", "").upper()
    shares_raw = data.get("shares", "")
    if not card_number or not symbol or not shares_raw:
        return render_home(user, error="Card number, market symbol, and shares are all required.", tab="stocks")
    try:
        shares = float(shares_raw)
    except ValueError:
        return render_home(user, error="Shares must be a valid number.", tab="stocks")
    if shares <= 0:
        return render_home(user, error="Shares must be greater than zero.", tab="stocks")
    conn = get_db()
    camper = get_camper_by_card(conn, card_number)
    asset = get_market_asset(conn, symbol)
    if not camper:
        conn.close()
        return render_home(user, error="No active camper was found for that card number.", tab="stocks")
    if not asset:
        conn.close()
        return render_home(user, error="That market symbol does not exist.", tab="stocks")
    live_price = live_market_price(asset)
    cost = round(live_price * shares, 2)
    position = get_position(conn, camper["id"], symbol)
    held_shares = position["shares"] if position else 0
    if side == "buy":
        if camper["balance"] < cost:
            conn.close()
            return render_home(user, error=f"{camper['name']} does not have enough balance to buy {number(shares)} shares of {symbol}.", tab="stocks")
        conn.execute("UPDATE campers SET balance = ? WHERE id = ?", (camper["balance"] - cost, camper["id"]))
        upsert_position(conn, camper["id"], symbol, held_shares + shares)
        insert_transaction(conn, camper["id"], "market_buy", cost, f"Bought {number(shares)} shares of {symbol} at {money(live_price)}", user["username"])
        log_action(conn, user, "market_buy", f"{camper['name']} bought {number(shares)} shares of {symbol}")
        conn.commit()
        conn.close()
        return render_home(user, message=f"{camper['name']} bought {number(shares)} shares of {symbol}.", action="market_buy", tab="stocks")
    if held_shares < shares:
        conn.close()
        return render_home(user, error=f"{camper['name']} does not own enough {symbol} shares to sell.", tab="stocks")
    conn.execute("UPDATE campers SET balance = ? WHERE id = ?", (camper["balance"] + cost, camper["id"]))
    upsert_position(conn, camper["id"], symbol, held_shares - shares)
    insert_transaction(conn, camper["id"], "market_sell", cost, f"Sold {number(shares)} shares of {symbol} at {money(live_price)}", user["username"])
    log_action(conn, user, "market_sell", f"{camper['name']} sold {number(shares)} shares of {symbol}")
    conn.commit()
    conn.close()
    return render_home(user, message=f"{camper['name']} sold {number(shares)} shares of {symbol}.", action="market_sell", tab="stocks")


def handle_login(environ, start_response):
    data = get_post_data(environ)
    username = data.get("username", "")
    password = data.get("password", "")
    conn = get_db()
    user = conn.execute(
        """
        SELECT *
        FROM staff_users
        WHERE username = ? AND password_hash = ? AND active = 1
        """,
        (username, hash_password(password)),
    ).fetchone()
    if not user:
        conn.close()
        body = render_login(error="Invalid username or password.").encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]
    token = secrets.token_hex(24)
    conn.execute(
        "INSERT INTO auth_sessions (staff_user_id, token, created_at) VALUES (?, ?, ?)",
        (user["id"], token, now()),
    )
    log_action(conn, user, "login", f"{user['username']} logged in")
    conn.commit()
    conn.close()
    return redirect_response(
        start_response,
        with_notice("/", message=f"Welcome, {user['username']}."),
        build_session_cookie(token),
    )


def handle_logout(environ, start_response):
    conn = get_db()
    user = get_current_user(conn, environ)
    token = get_cookie_value(environ, "camp_wallet_session")
    if token:
        conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
    if user:
        log_action(conn, user, "logout", f"{user['username']} logged out")
    conn.commit()
    conn.close()
    return redirect_response(
        start_response,
        with_notice("/login", message="You have been logged out."),
        clear_session_cookie(),
    )


def handle_add_camper(user, data):
    name = data.get("name", "")
    age_raw = data.get("age", "")
    card_number = data.get("card_number", "")
    starting_balance_raw = data.get("starting_balance", "")
    if not name or not age_raw or not card_number or not starting_balance_raw:
        return render_home(user, error="All camper fields are required.")
    try:
        age = int(age_raw)
        starting_balance = float(starting_balance_raw)
    except ValueError:
        return render_home(user, error="Age and starting balance must be valid numbers.")

    conn = get_db()
    available, error = make_card_available(conn, card_number)
    if not available:
        conn.close()
        return render_home(user, error=error)
    try:
        cursor = conn.execute(
            """
            INSERT INTO campers (name, age, card_number, balance, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, age, card_number, starting_balance, now()),
        )
        insert_transaction(conn, cursor.lastrowid, "starting_balance", starting_balance, "Camper created", user["username"])
        log_action(conn, user, "create_camper", f"Created {name} with card {card_number}")
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return render_home(user, error="That RFID card number is already assigned to another active camper.")
    conn.close()
    return render_home(user, message=f"Created camper {name} with card {card_number}.", action="create")


def handle_remove_camper(user, data):
    card_number = data.get("card_number", "")
    if not card_number:
        return render_home(user, error="RFID card number is required to remove a camper.")
    conn = get_db()
    camper = get_camper_by_card(conn, card_number)
    if not camper:
        conn.close()
        return render_home(user, error="No active camper was found for that card number.")
    conn.execute(
        "UPDATE campers SET active = 0, card_number = ? WHERE id = ?",
        (archived_card_number(camper["card_number"], camper["id"]), camper["id"]),
    )
    insert_transaction(conn, camper["id"], "removed", 0, "Camper account removed from active use", user["username"])
    log_action(conn, user, "remove_camper", f"Removed {camper['name']}")
    conn.commit()
    conn.close()
    return render_home(user, message=f"Removed {camper['name']} from the active camper list.", action="remove")


def handle_balance_change(user, data, change_type):
    card_number = data.get("card_number", "")
    amount_raw = data.get("amount", "")
    note = data.get("note", "")
    if not card_number or not amount_raw or not note:
        return render_home(user, error="Card number, amount, and note are required.")
    try:
        amount = float(amount_raw)
    except ValueError:
        return render_home(user, error="Amount must be a valid number.")
    if amount <= 0:
        return render_home(user, error="Amount must be greater than zero.")
    conn = get_db()
    camper = get_camper_by_card(conn, card_number)
    if not camper:
        conn.close()
        return render_home(user, error="No active camper was found for that card number.")
    signed_amount = amount if change_type == "add_funds" else -amount
    new_balance = camper["balance"] + signed_amount
    if new_balance < 0:
        conn.close()
        return render_home(user, error=f"{camper['name']} does not have enough money for that charge.")
    conn.execute("UPDATE campers SET balance = ? WHERE id = ?", (new_balance, camper["id"]))
    insert_transaction(conn, camper["id"], change_type, amount, note, user["username"])
    log_action(conn, user, change_type, f"{change_type} {money(amount)} for {camper['name']}")
    conn.commit()
    conn.close()
    if change_type == "add_funds":
        return render_home(user, message=f"Added {money(amount)} to {camper['name']}.", action="add_funds")
    return render_home(user, message=f"Charged {camper['name']} {money(amount)} for {note}.", action="charge")


def handle_transfer(user, data):
    from_card_number = data.get("from_card_number", "")
    to_card_number = data.get("to_card_number", "")
    amount_raw = data.get("amount", "")
    note = data.get("note", "")
    if not from_card_number or not to_card_number or not amount_raw or not note:
        return render_home(user, error="Both card numbers, amount, and reason are required for a transfer.")
    if from_card_number == to_card_number:
        return render_home(user, error="Transfer source and destination cards must be different.")
    try:
        amount = float(amount_raw)
    except ValueError:
        return render_home(user, error="Transfer amount must be a valid number.")
    if amount <= 0:
        return render_home(user, error="Transfer amount must be greater than zero.")
    conn = get_db()
    from_camper = get_camper_by_card(conn, from_card_number)
    to_camper = get_camper_by_card(conn, to_card_number)
    if not from_camper or not to_camper:
        conn.close()
        return render_home(user, error="Both campers must have active card numbers before you can transfer money.")
    if from_camper["balance"] < amount:
        conn.close()
        return render_home(user, error=f"{from_camper['name']} does not have enough money for that transfer.")
    conn.execute("UPDATE campers SET balance = ? WHERE id = ?", (from_camper["balance"] - amount, from_camper["id"]))
    conn.execute("UPDATE campers SET balance = ? WHERE id = ?", (to_camper["balance"] + amount, to_camper["id"]))
    insert_transaction(conn, from_camper["id"], "transfer_out", amount, f"To {to_camper['name']} ({to_camper['card_number']}): {note}", user["username"])
    insert_transaction(conn, to_camper["id"], "transfer_in", amount, f"From {from_camper['name']} ({from_camper['card_number']}): {note}", user["username"])
    log_action(conn, user, "transfer", f"Transferred {money(amount)} from {from_camper['name']} to {to_camper['name']}")
    conn.commit()
    conn.close()
    return render_home(user, message=f"Transferred {money(amount)} from {from_camper['name']} to {to_camper['name']}.", action="transfer")


def handle_weekly_reset(user, data):
    weekly_amount_raw = data.get("weekly_amount", "")
    if not weekly_amount_raw:
        return render_home(user, error="Weekly reset amount is required.")
    try:
        weekly_amount = float(weekly_amount_raw)
    except ValueError:
        return render_home(user, error="Weekly amount must be a valid number.")
    if weekly_amount < 0:
        return render_home(user, error="Weekly amount cannot be negative.")
    conn = get_db()
    campers = conn.execute("SELECT * FROM campers WHERE active = 1").fetchall()
    for camper in campers:
        conn.execute("UPDATE campers SET balance = ? WHERE id = ?", (weekly_amount, camper["id"]))
        insert_transaction(conn, camper["id"], "weekly_reset", weekly_amount, f"Weekly reset to {money(weekly_amount)}", user["username"])
    conn.execute("DELETE FROM market_positions")
    reset_time = now()
    conn.execute(
        """
        UPDATE market_assets
        SET previous_price = ?, current_price = ?, updated_at = ?, last_reason = ?
        """,
        (DEFAULT_MARKET_PRICE, DEFAULT_MARKET_PRICE, reset_time, "Fresh week reset"),
    )
    conn.execute("DELETE FROM market_snapshots")
    for symbol in ["PIA", "OIL", "GOLD", "TECH"]:
        conn.execute(
            """
            INSERT INTO market_snapshots (asset_symbol, price, reason, source, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (symbol, DEFAULT_MARKET_PRICE, "Fresh week reset", "reset", reset_time),
        )
    log_action(conn, user, "weekly_reset", f"Reset {len(campers)} campers to {money(weekly_amount)} and restarted the market")
    conn.commit()
    conn.close()
    return render_home(
        user,
        message=f"Reset {len(campers)} campers to {money(weekly_amount)} and restarted the market for a fresh week.",
        action="weekly_reset",
    )


def handle_replace_card(user, data):
    camper_name = data.get("name", "")
    new_card_number = data.get("new_card_number", "")
    if not camper_name or not new_card_number:
        return render_home(user, error="Camper name and new RFID card number are required.")
    conn = get_db()
    camper = get_camper_by_name(conn, camper_name)
    if not camper:
        conn.close()
        return render_home(user, error="No active camper was found with that name.")
    available, error = make_card_available(conn, new_card_number)
    if not available:
        conn.close()
        return render_home(user, error=error)
    old_card_number = camper["card_number"]
    conn.execute(
        "UPDATE campers SET card_number = ? WHERE id = ?",
        (new_card_number, camper["id"]),
    )
    insert_transaction(
        conn,
        camper["id"],
        "replace_card",
        0,
        f"Replaced lost card {old_card_number} with new card {new_card_number}",
        user["username"],
    )
    log_action(conn, user, "replace_card", f"Moved {camper['name']} from {old_card_number} to {new_card_number}")
    conn.commit()
    conn.close()
    return render_home(user, message=f"Assigned a new card to {camper['name']}.", action="replace_card")


def handle_voting_start(user, data):
    title = data.get("title", "").strip()
    details = data.get("details", "").strip()
    if not title:
        return render_home(user, error="Add a bill name before starting voting.", tab="voting")
    conn = get_db()
    existing = get_active_voting_session(conn)
    if existing:
        conn.close()
        return render_home(user, error="There is already a live voting session. End it before starting another one.", tab="voting")
    timestamp = now()
    conn.execute(
        """
        INSERT INTO voting_sessions (title, details, status, created_by, created_at, started_at, ended_at)
        VALUES (?, ?, 'ACTIVE', ?, ?, ?, '')
        """,
        (title, details, user["username"], timestamp, timestamp),
    )
    log_action(conn, user, "voting_start", f"Started bill vote: {title}")
    conn.commit()
    conn.close()
    return render_home(user, message=f"Voting is now open for '{title}'.", action="voting_start", tab="voting")


def handle_voting_end(user):
    conn = get_db()
    session = get_active_voting_session(conn)
    if not session:
        conn.close()
        return render_home(user, error="There is no live voting session to end.", tab="voting")
    conn.execute(
        "UPDATE voting_sessions SET status = 'ENDED', ended_at = ? WHERE id = ?",
        (now(), session["id"]),
    )
    log_action(conn, user, "voting_end", f"Ended bill vote: {session['title']}")
    conn.commit()
    conn.close()
    return render_home(user, message=f"Voting closed for '{session['title']}'.", action="voting_end", tab="voting")


def handle_voting_vote(user, data):
    card_number = data.get("card_number", "").strip()
    vote_value = data.get("vote_value", "").strip().upper()
    if not card_number or vote_value not in {"YES", "NO"}:
        return render_voting_controller(user, error="Scan a card and choose Yes or No.")
    conn = get_db()
    session = get_active_voting_session(conn)
    if not session:
        conn.close()
        return render_voting_controller(user, error="Start a voting session before collecting votes.")
    camper = get_camper_by_card(conn, card_number)
    if not camper:
        conn.close()
        return render_voting_controller(user, error="No active camper was found for that card.")
    existing_vote = conn.execute(
        """
        SELECT *
        FROM voting_votes
        WHERE session_id = ? AND camper_id = ?
        """,
        (session["id"], camper["id"]),
    ).fetchone()
    timestamp = now()
    if existing_vote:
        conn.execute(
            """
            UPDATE voting_votes
            SET vote_value = ?, card_number = ?, camper_name = ?, cast_by = ?, updated_at = ?, created_at = ?
            WHERE id = ?
            """,
            (vote_value, camper["card_number"], camper["name"], user["username"], timestamp, timestamp, existing_vote["id"]),
        )
        log_action(conn, user, "voting_vote", f"{camper['name']} updated vote to {vote_value} on {session['title']}")
        message = f"{camper['name']} voted {vote_value}."
    else:
        conn.execute(
            """
            INSERT INTO voting_votes (session_id, camper_id, card_number, camper_name, vote_value, cast_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session["id"], camper["id"], camper["card_number"], camper["name"], vote_value, user["username"], timestamp, timestamp),
        )
        log_action(conn, user, "voting_vote", f"{camper['name']} voted {vote_value} on {session['title']}")
        message = f"{camper['name']} voted {vote_value}."
    conn.commit()
    conn.close()
    return render_voting_controller(user, message=message, action="voting_vote")


def handle_create_staff(user, data):
    if user["role"] != "ADMIN":
        return render_home(user, error="Only the main admin account can create staff logins.")
    username = data.get("username", "")
    password = data.get("password", "")
    role = data.get("role", "")
    if not username or not password or role not in {"LEADER", "UNDER LEADER"}:
        return render_home(user, error="Username, password, and a valid role are required.")
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO staff_users (username, password_hash, role, active, created_at)
            VALUES (?, ?, ?, 1, ?)
            """,
            (username, hash_password(password), role, now()),
        )
        log_action(conn, user, "create_staff", f"Created {role} account for {username}")
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return render_home(user, error="That username already exists.")
    conn.close()
    return render_home(user, message=f"Created staff login for {username}.", action="staff_created")


def handle_create_student(user, data):
    camper_id = data.get("camper_id", "")
    login_name = data.get("login_name", "")
    password = data.get("password", "")
    if not camper_id or not login_name or not password:
        return render_home(user, error="Student, name, and password are required.", tab="students")
    try:
        camper_id_int = int(camper_id)
    except ValueError:
        return render_home(user, error="Student selection was invalid.", tab="students")
    conn = get_db()
    camper = conn.execute("SELECT * FROM campers WHERE id = ? AND active = 1", (camper_id_int,)).fetchone()
    if not camper:
        conn.close()
        return render_home(user, error="That camper account is not active.", tab="students")
    existing = get_student_user_by_camper(conn, camper_id_int)
    timestamp = now()
    try:
        if existing:
            conn.execute(
                """
                UPDATE student_users
                SET login_name = ?, password_hash = ?, created_by = ?, updated_at = ?
                WHERE camper_id = ?
                """,
                (login_name, hash_password(password), user["username"], timestamp, camper_id_int),
            )
        else:
            conn.execute(
                """
                INSERT INTO student_users (camper_id, login_name, password_hash, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (camper_id_int, login_name, hash_password(password), user["username"], timestamp, timestamp),
            )
        log_action(conn, user, "student_create", f"Created student portal for {camper['name']}")
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return render_home(user, error="That student name is already in use for another login.", tab="students")
    conn.close()
    return render_home(user, message=f"Student portal is ready for {camper['name']}.", action="student_created", tab="students")


def handle_link_student_card(user, data):
    camper_id = data.get("camper_id", "")
    card_number = data.get("card_number", "")
    if not camper_id or not card_number:
        return render_home(user, error="Student and card number are required.", tab="students")
    try:
        camper_id_int = int(camper_id)
    except ValueError:
        return render_home(user, error="Student selection was invalid.", tab="students")
    conn = get_db()
    camper = conn.execute("SELECT * FROM campers WHERE id = ? AND active = 1", (camper_id_int,)).fetchone()
    if not camper:
        conn.close()
        return render_home(user, error="That camper account is not active.", tab="students")
    available, error = make_card_available(conn, card_number)
    if not available:
        conn.close()
        return render_home(user, error=error, tab="students")
    conn.execute("UPDATE campers SET card_number = ? WHERE id = ?", (card_number, camper_id_int))
    insert_transaction(conn, camper_id_int, "student_link_card", 0, f"Linked student portal to card {card_number}", user["username"])
    log_action(conn, user, "student_link_card", f"Linked {camper['name']} to card {card_number}")
    conn.commit()
    conn.close()
    return render_home(user, message=f"Linked {camper['name']} to card {card_number}.", action="student_linked", tab="students")


def handle_create_promo(user, data):
    camper_id = data.get("camper_id", "")
    title = data.get("title", "")
    details = data.get("details", "")
    if not camper_id or not title:
        return render_home(user, error="Student and promo title are required.", tab="students")
    try:
        camper_id_int = int(camper_id)
    except ValueError:
        return render_home(user, error="Student selection was invalid.", tab="students")
    conn = get_db()
    camper = conn.execute("SELECT * FROM campers WHERE id = ? AND active = 1", (camper_id_int,)).fetchone()
    if not camper:
        conn.close()
        return render_home(user, error="That camper account is not active.", tab="students")
    conn.execute(
        """
        INSERT INTO student_promos (camper_id, title, details, status, granted_by, created_at, redeemed_at)
        VALUES (?, ?, ?, 'AVAILABLE', ?, ?, '')
        """,
        (camper_id_int, title, details, user["username"], now()),
    )
    insert_transaction(conn, camper_id_int, "promo_granted", 0, f"Promo granted: {title}", user["username"])
    log_action(conn, user, "promo_create", f"Gave {camper['name']} promo {title}")
    conn.commit()
    conn.close()
    return render_home(user, message=f"Promo added for {camper['name']}.", action="promo_granted", tab="students")


def handle_redeem_promo(user, data):
    promo_id = data.get("promo_id", "")
    if not promo_id:
        return render_home(user, error="Promo id is required.", tab="students")
    try:
        promo_id_int = int(promo_id)
    except ValueError:
        return render_home(user, error="Promo id was invalid.", tab="students")
    conn = get_db()
    promo = conn.execute(
        """
        SELECT student_promos.*, campers.name
        FROM student_promos
        JOIN campers ON campers.id = student_promos.camper_id
        WHERE student_promos.id = ?
        """,
        (promo_id_int,),
    ).fetchone()
    if not promo:
        conn.close()
        return render_home(user, error="Promo not found.", tab="students")
    if promo["status"] != "AVAILABLE":
        conn.close()
        return render_home(user, error="That promo has already been redeemed.", tab="students")
    redeemed_at = now()
    conn.execute(
        "UPDATE student_promos SET status = 'REDEEMED', redeemed_at = ? WHERE id = ?",
        (redeemed_at, promo_id_int),
    )
    insert_transaction(conn, promo["camper_id"], "promo_redeemed", 0, f"Promo redeemed: {promo['title']}", user["username"])
    log_action(conn, user, "promo_redeem", f"Redeemed {promo['title']} for {promo['name']}")
    conn.commit()
    conn.close()
    return render_home(user, message=f"Redeemed promo for {promo['name']}.", action="promo_redeemed", tab="students")


def handle_student_login(environ, start_response):
    data = get_post_data(environ)
    login_name = data.get("login_name", "")
    password = data.get("password", "")
    conn = get_db()
    student = get_student_user_by_login(conn, login_name)
    if not student or not student["active"] or student["password_hash"] != hash_password(password):
        conn.close()
        body = render_student_login(error="Invalid student name or password.").encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]
    token = secrets.token_hex(24)
    conn.execute(
        "INSERT INTO student_sessions (student_user_id, token, created_at) VALUES (?, ?, ?)",
        (student["id"], token, now()),
    )
    conn.commit()
    conn.close()
    return redirect_response(
        start_response,
        with_notice("/student", message=f"Welcome, {student['name']}."),
        build_student_session_cookie(token),
    )


def handle_student_logout(environ, start_response):
    conn = get_db()
    token = get_cookie_value(environ, "camp_student_session")
    if token:
        conn.execute("DELETE FROM student_sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return redirect_response(
        start_response,
        with_notice("/student", message="Student session ended."),
        clear_student_session_cookie(),
    )


def handle_student_banner(student, data):
    banner_theme = data.get("banner_theme", "violet")
    if banner_theme not in {"violet", "ocean", "sunset", "emerald"}:
        return "", "Choose a valid banner theme.", ""
    conn = get_db()
    conn.execute(
        """
        UPDATE student_users
        SET banner_theme = ?, updated_at = ?
        WHERE id = ?
        """,
        (banner_theme, now(), student["id"]),
    )
    conn.commit()
    conn.close()
    return "Banner theme updated.", "", ""


def handle_student_promo_use(student, data):
    promo_id = data.get("promo_id", "")
    swipe_value = clamp_float(data.get("swipe_value"), 0, 100, 0)
    if not promo_id:
        return "", "Promo id is required.", ""
    if swipe_value < 100:
        return "", "Swipe all the way before using a promo.", ""
    try:
        promo_id_int = int(promo_id)
    except ValueError:
        return "", "Promo id was invalid.", ""
    conn = get_db()
    promo = conn.execute(
        """
        SELECT *
        FROM student_promos
        WHERE id = ? AND camper_id = ?
        """,
        (promo_id_int, student["camper_id"]),
    ).fetchone()
    if not promo:
        conn.close()
        return "", "Promo not found for this account.", ""
    if promo["status"] != "AVAILABLE":
        conn.close()
        return "", "That promo has already been used.", ""
    redeemed_at = now()
    conn.execute(
        "UPDATE student_promos SET status = 'REDEEMED', redeemed_at = ? WHERE id = ?",
        (redeemed_at, promo_id_int),
    )
    insert_transaction(conn, student["camper_id"], "promo_redeemed", 0, f"Promo redeemed: {promo['title']}", student["login_name"])
    conn.commit()
    conn.close()
    return f"Promo used: {promo['title']}.", "", "promo_redeemed"


def handle_student_photo(student, environ):
    fields, files = get_request_data(environ)
    photo = files.get("photo")
    if not photo or not photo.get("content"):
        return "", "Choose a photo before uploading.", ""
    if len(photo["content"]) > STUDENT_PHOTO_MAX_BYTES:
        return "", "Photo is too large. Keep it under 4 MB.", ""
    content_type = (photo.get("content_type") or "").lower()
    if not content_type.startswith("image/"):
        return "", "Only image uploads are allowed.", ""
    ext = safe_file_ext(photo.get("filename"), content_type)
    filename = f"student_{student['camper_id']}_{secrets.token_hex(6)}{ext}"
    relative_path = os.path.join("uploads", filename)
    absolute_path = os.path.join(DATA_DIR, relative_path)
    with open(absolute_path, "wb") as output_file:
        output_file.write(photo["content"])
    photo_zoom = clamp_float(fields.get("photo_zoom"), 1.0, 2.5, 1.0)
    photo_x = clamp_float(fields.get("photo_x"), 0.0, 100.0, 50.0)
    photo_y = clamp_float(fields.get("photo_y"), 0.0, 100.0, 50.0)
    conn = get_db()
    old = get_student_user_by_camper(conn, student["camper_id"])
    if old and old["photo_path"]:
        old_file = os.path.join(DATA_DIR, old["photo_path"])
        if os.path.isfile(old_file):
            try:
                os.remove(old_file)
            except OSError:
                pass
    conn.execute(
        """
        UPDATE student_users
        SET photo_path = ?, photo_zoom = ?, photo_x = ?, photo_y = ?, updated_at = ?
        WHERE camper_id = ?
        """,
        (relative_path, photo_zoom, photo_x, photo_y, now(), student["camper_id"]),
    )
    conn.commit()
    conn.close()
    return "Profile photo uploaded.", "", "student_photo"


def application(environ, start_response):
    init_db()
    method = environ["REQUEST_METHOD"]
    path = environ.get("PATH_INFO", "/")
    query = parse_qs(environ.get("QUERY_STRING", ""))

    if method == "GET" and path == "/health":
        body = json.dumps({"ok": True, "time": now()}).encode("utf-8")
        start_response("200 OK", [("Content-Type", "application/json; charset=utf-8")])
        return [body]

    if method == "GET" and path.startswith("/assets/"):
        asset_path = os.path.join(BASE_DIR, path.lstrip("/"))
        if os.path.isfile(asset_path):
            content_type = mimetypes.guess_type(asset_path)[0] or "application/octet-stream"
            with open(asset_path, "rb") as asset_file:
                data = asset_file.read()
            start_response("200 OK", [("Content-Type", content_type)])
            return [data]
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"Asset not found"]

    if method == "GET" and path.startswith("/uploads/"):
        asset_path = os.path.join(DATA_DIR, path.lstrip("/"))
        if os.path.isfile(asset_path):
            content_type = mimetypes.guess_type(asset_path)[0] or "application/octet-stream"
            with open(asset_path, "rb") as asset_file:
                data = asset_file.read()
            start_response("200 OK", [("Content-Type", content_type)])
            return [data]
        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"Upload not found"]

    if method == "GET" and path == "/login":
        message = (query.get("message", [""])[0]).strip()
        error = (query.get("error", [""])[0]).strip()
        body = render_login(message=message, error=error).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/login":
        return handle_login(environ, start_response)

    if method == "GET" and path == "/student":
        conn = get_db()
        student = get_current_student(conn, environ)
        conn.close()
        message = (query.get("message", [""])[0]).strip()
        error = (query.get("error", [""])[0]).strip()
        action = (query.get("action", [""])[0]).strip()
        body = (
            render_student_home(student, message=message, error=error, action=action)
            if student
            else render_student_login(message=message, error=error)
        ).encode("utf-8")
        headers = [("Content-Type", "text/html; charset=utf-8")]
        if not student:
            headers.append(("Set-Cookie", clear_student_session_cookie()))
        start_response("200 OK", headers)
        return [body]

    if method == "POST" and path == "/student/login":
        return handle_student_login(environ, start_response)

    if method == "POST" and path == "/student/logout":
        return handle_student_logout(environ, start_response)

    if method == "POST" and path in {"/student/photo", "/student/banner", "/student/promo/use", "/student/market/buy", "/student/market/sell"}:
        conn = get_db()
        student = get_current_student(conn, environ)
        conn.close()
        if not student:
            body = render_student_login(error="Please log in to continue.").encode("utf-8")
            headers = [("Content-Type", "text/html; charset=utf-8"), ("Set-Cookie", clear_student_session_cookie())]
            start_response("200 OK", headers)
            return [body]
        if path == "/student/photo":
            message, error, action = handle_student_photo(student, environ)
        elif path == "/student/banner":
            message, error, action = handle_student_banner(student, get_post_data(environ))
        elif path == "/student/promo/use":
            message, error, action = handle_student_promo_use(student, get_post_data(environ))
        else:
            message, error, action = "", "Students can view stocks here, but leaders must handle stock trades from the leader dashboard.", ""
        return redirect_response(start_response, with_notice("/student", message=message, error=error, action=action))

    conn = get_db()
    user = get_current_user(conn, environ)
    conn.close()
    if not user:
        body = render_login(error="Please log in to continue.").encode("utf-8")
        headers = [("Content-Type", "text/html; charset=utf-8"), ("Set-Cookie", clear_session_cookie())]
        start_response("200 OK", headers)
        return [body]

    if method == "POST" and path == "/logout":
        return handle_logout(environ, start_response)

    if method == "GET" and path == "/":
        selected_tab = (query.get("tab", ["bank"])[0] or "bank").strip().lower()
        message = (query.get("message", [""])[0]).strip()
        error = (query.get("error", [""])[0]).strip()
        action = (query.get("action", [""])[0]).strip()
        body = render_home(user, message=message, error=error, action=action, tab=selected_tab).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "GET" and path == "/voting/controller":
        message = (query.get("message", [""])[0]).strip()
        error = (query.get("error", [""])[0]).strip()
        action = (query.get("action", [""])[0]).strip()
        body = render_voting_controller(user, message=message, error=error, action=action).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "GET" and path == "/voting/projector":
        body = render_voting_projector(user).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "GET" and path == "/voting/live":
        conn = get_db()
        payload = voting_payload(conn, get_active_voting_session(conn))
        conn.close()
        body = json.dumps(payload).encode("utf-8")
        start_response("200 OK", [("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")])
        return [body]

    if method == "GET" and path == "/lookup":
        card_number = (query.get("card_number", [""])[0]).strip()
        body = render_lookup(user, card_number).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/campers/add":
        body = handle_add_camper(user, get_post_data(environ)).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/campers/remove":
        body = handle_remove_camper(user, get_post_data(environ)).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/campers/replace-card":
        body = handle_replace_card(user, get_post_data(environ)).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/transactions/charge":
        body = handle_balance_change(user, get_post_data(environ), "charge").encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/transactions/add-funds":
        body = handle_balance_change(user, get_post_data(environ), "add_funds").encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/transactions/transfer":
        body = handle_transfer(user, get_post_data(environ)).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/weekly-reset":
        body = handle_weekly_reset(user, get_post_data(environ)).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/voting/start":
        body = handle_voting_start(user, get_post_data(environ)).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/voting/end":
        body = handle_voting_end(user).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/voting/vote":
        body = handle_voting_vote(user, get_post_data(environ)).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/market/event":
        body = handle_market_event(user, get_post_data(environ)).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/market/refresh":
        body = handle_market_refresh(user).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/market/buy":
        body = handle_trade(user, get_post_data(environ), "buy").encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/market/sell":
        body = handle_trade(user, get_post_data(environ), "sell").encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/staff/create":
        body = handle_create_staff(user, get_post_data(environ)).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/students/create":
        body = handle_create_student(user, get_post_data(environ)).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/students/link-card":
        body = handle_link_student_card(user, get_post_data(environ)).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/students/promos/create":
        body = handle_create_promo(user, get_post_data(environ)).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if method == "POST" and path == "/students/promos/redeem":
        body = handle_redeem_promo(user, get_post_data(environ)).encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    if path == "/favicon.ico":
        start_response("204 No Content", [])
        return [b""]

    start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
    return [b"Not found"]


if __name__ == "__main__":
    init_db()
    print(f"Camp Card System running at http://{HOST}:{PORT}")
    with make_server(HOST, PORT, application) as server:
        server.serve_forever()
