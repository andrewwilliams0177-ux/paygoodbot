"""
Ручная выдача карт: заявки клиентов уходят в админ-группу,
админ нажимает «Выдать», присылает данные карты — бот отдаёт её клиенту.

Настройка через переменные Railway:
  ADMIN_CHAT_ID  — id админ-группы (число, обычно с минусом). Пусто = ручной режим выключен.
  ADMIN_IDS      — id помощников через запятую (кто может выдавать). Пусто = любой участник группы.
"""
import os
import re
import time
import uuid

import aiohttp

import config
from db import database as db
from db.database import get_conn


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def admin_chat_id() -> int:
    return _int(os.getenv("ADMIN_CHAT_ID", "0"))


def admin_ids() -> set:
    return {_int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if _int(x)}


def manual_mode() -> bool:
    return admin_chat_id() != 0


def is_admin(user_id: int) -> bool:
    ids = admin_ids()
    return (user_id in ids) if ids else True


def init_orders_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS card_orders (
                order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                client_name TEXT,
                client_username TEXT,
                plan_id TEXT,
                plan_title TEXT,
                price INTEGER,
                status TEXT DEFAULT 'pending',
                admin_id INTEGER,
                admin_name TEXT,
                admin_msg_id INTEGER,
                created_at INTEGER
            )
        """)
        conn.commit()


async def tg(method: str, **payload):
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}"
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
            d = await r.json()
    if not d.get("ok"):
        raise RuntimeError(d.get("description", "telegram error"))
    return d["result"]


def who(name, username):
    return f"@{username}" if username else (name or "без имени")


def request_text(o, footer=""):
    text = (
        f"🆕 Заявка #{o['order_id']} на виртуальную карту\n\n"
        f"Тариф: {o['plan_title']} — {o['price']} ₽\n"
        f"Клиент: {o['client_name']} ({who('', o['client_username']) if o['client_username'] else 'нет @username'})\n"
        f"ID клиента: {o['user_id']}"
    )
    return text + (("\n\n" + footer) if footer else "")


def request_keyboard(order_id, taken=False):
    if taken:
        rows = [[{"text": "❌ Отклонить и вернуть деньги", "callback_data": f"ord_rej:{order_id}"}]]
    else:
        rows = [[
            {"text": "✅ Выдать", "callback_data": f"ord_take:{order_id}"},
            {"text": "❌ Отклонить", "callback_data": f"ord_rej:{order_id}"},
        ]]
    return {"inline_keyboard": rows}


def get_order(order_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM card_orders WHERE order_id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def get_order_by_msg(msg_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM card_orders WHERE admin_msg_id = ?", (msg_id,)).fetchone()
    return dict(row) if row else None


def _set_tx(order_id, status):
    with get_conn() as conn:
        conn.execute("UPDATE transactions SET status = ? WHERE tx_id = ?", (status, f"order-{order_id}"))
        conn.commit()


async def place_order(user_id, name, username, plan):
    """Списывает баланс, создаёт заявку и шлёт её в админ-группу.
    Возвращает (order_id, None) или (None, текст_ошибки)."""
    if not db.deduct_balance(user_id, plan["price"]):
        return None, "Недостаточно средств"
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO card_orders (user_id, client_name, client_username, plan_id, plan_title, price, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (user_id, name, username, plan["id"], plan["title"], plan["price"], int(time.time())),
        )
        order_id = cur.lastrowid
        conn.execute(
            "INSERT INTO transactions (tx_id, user_id, amount, kind, status, created_at, title) "
            "VALUES (?, ?, ?, 'buy_card', 'pending', ?, ?)",
            (f"order-{order_id}", user_id, plan["price"], int(time.time()), plan["title"]),
        )
        conn.commit()
    o = get_order(order_id)
    try:
        msg = await tg("sendMessage", chat_id=admin_chat_id(), text=request_text(o),
                       reply_markup=request_keyboard(order_id))
    except Exception:
        with get_conn() as conn:
            conn.execute("DELETE FROM card_orders WHERE order_id = ?", (order_id,))
            conn.execute("DELETE FROM transactions WHERE tx_id = ?", (f"order-{order_id}",))
            conn.commit()
        db.add_balance(user_id, plan["price"])
        return None, "Не удалось отправить заявку, деньги не списаны"
    with get_conn() as conn:
        conn.execute("UPDATE card_orders SET admin_msg_id = ? WHERE order_id = ?", (msg["message_id"], order_id))
        conn.commit()
    return order_id, None


def take_order(order_id, admin_id, admin_name):
    """Первый нажавший «Выдать» занимает заявку. Возвращает (ok, заявка)."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE card_orders SET status='processing', admin_id=?, admin_name=? WHERE order_id=? AND status='pending'",
            (admin_id, admin_name, order_id),
        )
        conn.commit()
        ok = cur.rowcount == 1
    return ok, get_order(order_id)


def reject_order(order_id, admin_id):
    """Отклоняет заявку и возвращает деньги. Заявку 'в работе' может отклонить только взявший."""
    o = get_order(order_id)
    if not o or o["status"] not in ("pending", "processing"):
        return False, o
    if o["status"] == "processing" and o["admin_id"] != admin_id:
        return False, o
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE card_orders SET status='rejected' WHERE order_id=? AND status IN ('pending','processing')",
            (order_id,),
        )
        conn.commit()
        if cur.rowcount != 1:
            return False, o
    db.add_balance(o["user_id"], o["price"])
    _set_tx(order_id, "refunded")
    return True, get_order(order_id)


def parse_card(text: str):
    """Достаёт номер, срок и CVV из свободного текста. Возвращает dict или None."""
    t = (text or "").replace("\n", " ")
    m = re.search(r"\b(\d{1,2})\s*/\s*(\d{4}|\d{2})\b", t)
    if not m:
        return None
    mm, yy = int(m.group(1)), m.group(2)[-2:]
    if not 1 <= mm <= 12:
        return None
    exp = f"{mm:02d}/{yy}"
    rest = t[:m.start()] + " " + t[m.end():]
    p = re.search(r"\b(?:\d{4}[ -]?){3}\d{4}\b", rest) or re.search(r"\b\d{13,19}\b", rest)
    if not p:
        return None
    pan = re.sub(r"\D", "", p.group(0))
    rest = rest[:p.start()] + " " + rest[p.end():]
    c = re.search(r"\b\d{3,4}\b", rest)
    if not c:
        return None
    return {"pan": pan, "exp": exp, "cvv": c.group(0)}


def issue_order(order_id, admin_id, card):
    """Сохраняет карту клиенту и закрывает заявку. Возвращает (ok, заявка)."""
    o = get_order(order_id)
    if not o or o["status"] != "processing" or o["admin_id"] != admin_id:
        return False, o
    with get_conn() as conn:
        cur = conn.execute("UPDATE card_orders SET status='issued' WHERE order_id=? AND status='processing'", (order_id,))
        conn.commit()
        if cur.rowcount != 1:
            return False, o
    db.save_card({"card_id": str(uuid.uuid4()), "user_id": o["user_id"], "plan_id": o["plan_id"],
                  "pan": card["pan"], "exp": card["exp"], "cvv": card["cvv"]})
    _set_tx(order_id, "paid")
    return True, get_order(order_id)


def pending_orders(user_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT order_id, plan_id, plan_title, status FROM card_orders "
            "WHERE user_id = ? AND status IN ('pending','processing') ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]
