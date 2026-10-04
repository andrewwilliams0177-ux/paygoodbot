"""Вход на сайт через приложение Telegram (без номера телефона).

Сайт получает одноразовую ссылку t.me/<бот>?start=wl_<токен>. Человек открывает её,
бот просит подтвердить, сайт получает «билет» и дальше вводит пароль, как обычно.
"""
import hashlib
import secrets
import time

from db.database import get_conn

LINK_TTL = 600            # ссылка живёт 10 минут
TICKET_TTL = 24 * 3600    # билет входа живёт 24 часа


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def init_weblogin_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_logins (
                token TEXT PRIMARY KEY,
                secret_hash TEXT,
                tg_id INTEGER,
                name TEXT,
                username TEXT,
                created INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_tickets (
                ticket_hash TEXT PRIMARY KEY,
                tg_id INTEGER,
                name TEXT,
                username TEXT,
                expires INTEGER
            )
        """)
        conn.commit()


def start():
    """Создаёт ссылку входа. Возвращает (token, secret)."""
    token, secret = secrets.token_urlsafe(12), secrets.token_urlsafe(24)
    now = int(time.time())
    with get_conn() as conn:
        conn.execute("DELETE FROM web_logins WHERE created < ?", (now - LINK_TTL,))
        conn.execute("DELETE FROM web_tickets WHERE expires < ?", (now,))
        conn.execute("INSERT INTO web_logins (token, secret_hash, created) VALUES (?, ?, ?)",
                     (token, _h(secret), now))
        conn.commit()
    return token, secret


def exists(token: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT created FROM web_logins WHERE token = ?", (token,)).fetchone()
    return bool(row and row["created"] > time.time() - LINK_TTL)


def confirm(token: str, tg_id: int, name: str, username):
    """Бот вызывает, когда человек нажал «Подтвердить»."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE web_logins SET tg_id=?, name=?, username=? WHERE token=? AND tg_id IS NULL AND created>?",
            (tg_id, name, username, token, int(time.time()) - LINK_TTL),
        )
        conn.commit()
        return cur.rowcount == 1


def poll(token: str, secret: str):
    """Сайт спрашивает, подтвердил ли человек. Возвращает ('expired'|'pending'|'ok', билет)."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM web_logins WHERE token = ?", (token,)).fetchone()
        if not row or row["secret_hash"] != _h(secret) or row["created"] < time.time() - LINK_TTL:
            return "expired", None
        if row["tg_id"] is None:
            return "pending", None
        ticket = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO web_tickets (ticket_hash, tg_id, name, username, expires) VALUES (?, ?, ?, ?, ?)",
                     (_h(ticket), row["tg_id"], row["name"], row["username"], int(time.time()) + TICKET_TTL))
        conn.execute("DELETE FROM web_logins WHERE token = ?", (token,))
        conn.commit()
    return "ok", ticket


def user_by_ticket(ticket: str):
    if not ticket:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM web_tickets WHERE ticket_hash = ?", (_h(str(ticket)),)).fetchone()
    if not row or row["expires"] < time.time():
        return None
    return {"id": row["tg_id"], "first_name": row["name"] or "", "last_name": "", "username": row["username"]}
