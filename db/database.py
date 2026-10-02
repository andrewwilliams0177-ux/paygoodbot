import sqlite3
import time
from contextlib import contextmanager

from config import DB_PATH


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 0,
                created_at INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                card_id TEXT PRIMARY KEY,
                user_id INTEGER,
                plan_id TEXT,
                pan TEXT,
                exp TEXT,
                cvv TEXT,
                status TEXT DEFAULT 'active',
                created_at INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                tx_id TEXT PRIMARY KEY,
                user_id INTEGER,
                amount INTEGER,
                kind TEXT,
                status TEXT,
                created_at INTEGER
            )
        """)
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_or_create_user(user_id: int, username: str | None):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            return dict(row)
        conn.execute(
            "INSERT INTO users (user_id, username, balance, created_at) VALUES (?, ?, 0, ?)",
            (user_id, username, int(time.time())),
        )
        conn.commit()
        return {"user_id": user_id, "username": username, "balance": 0}


def get_balance(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row["balance"] if row else 0


def add_balance(user_id: int, amount: int):
    with get_conn() as conn:
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()


def deduct_balance(user_id: int, amount: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not row or row["balance"] < amount:
            return False
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        return True


def save_card(card: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO cards (card_id, user_id, plan_id, pan, exp, cvv, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
            (card["card_id"], card["user_id"], card["plan_id"], card["pan"],
             card["exp"], card["cvv"], int(time.time())),
        )
        conn.commit()


def get_user_cards(user_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM cards WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def log_transaction(tx_id: str, user_id: int, amount: int, kind: str, status: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO transactions (tx_id, user_id, amount, kind, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (tx_id, user_id, amount, kind, status, int(time.time())),
        )
        conn.commit()
