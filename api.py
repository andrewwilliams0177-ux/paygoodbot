"""
Серверная часть Mini App.

Личность пользователя подтверждает Telegram: Mini App передаёт initData, подписанные
токеном бота (подделать нельзя). Поверх этого стоит пароль и, по желанию, 2FA (TOTP).
После входа сервер выдаёт токен сессии; баланс, карты и история хранятся только здесь.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
import time
import uuid
from urllib.parse import parse_qsl, quote

from aiohttp import web

import config
from db import database as db
from db.database import get_conn
from services.card_provider import get_card_provider

ALLOWED_ORIGIN = os.getenv("MINIAPP_ORIGIN", "https://andrewwilliams0177-ux.github.io")
# 1 = тестовое пополнение без реальной оплаты. Поставь 0, когда подключишь настоящий платёж.
MOCK_PAYMENTS = os.getenv("MOCK_PAYMENTS", "1") == "1"

INITDATA_MAX_AGE = 24 * 3600
SESSION_TTL = 7 * 24 * 3600
MAX_ATTEMPTS = 5
LOCK_SECONDS = 300
MIN_PASSWORD = 6
TOPUP_AMOUNTS = (300, 600, 1000, 2000)
METHODS = {"sbp": "СБП", "card": "Карта РФ", "crypto": "Криптовалюта", "stars": "Telegram Stars"}


# ───────────────────────── база ─────────────────────────

def init_api_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_accounts (
                tg_id INTEGER PRIMARY KEY,
                name TEXT,
                pass_hash BLOB,
                salt BLOB,
                failed INTEGER DEFAULT 0,
                locked_until INTEGER DEFAULT 0,
                created_at INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_sessions (
                token_hash TEXT PRIMARY KEY,
                tg_id INTEGER,
                expires INTEGER
            )
        """)
        for ddl in (
            "ALTER TABLE web_accounts ADD COLUMN totp_secret TEXT",
            "ALTER TABLE web_accounts ADD COLUMN totp_pending TEXT",
            "ALTER TABLE web_accounts ADD COLUMN totp_last INTEGER DEFAULT 0",
            "ALTER TABLE transactions ADD COLUMN title TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # колонка уже есть
        conn.commit()


# ───────────────────────── Telegram и пароли ─────────────────────────

def validate_init_data(init_data: str):
    """Возвращает dict пользователя Telegram или None, если подпись неверна."""
    if not init_data:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        got_hash = parsed.pop("hash", None)
        if not got_hash:
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, got_hash):
            return None
        if time.time() - int(parsed.get("auth_date", "0")) > INITDATA_MAX_AGE:
            return None
        return json.loads(parsed["user"])
    except Exception:
        return None


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def _display_name(u: dict) -> str:
    return (u.get("first_name", "") + " " + u.get("last_name", "")).strip() or "Пользователь"


# ───────────────────────── TOTP (двухфакторка, RFC 6238) ─────────────────────────

def _hotp(secret_b32: str, step: int) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    num = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{num:06d}"


def verify_totp(secret_b32: str, code: str, last_step: int = 0):
    """Возвращает номер шага, если код верный и ещё не использовался, иначе None."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    now = int(time.time()) // 30
    for step in (now - 1, now, now + 1):
        if step > last_step and hmac.compare_digest(_hotp(secret_b32, step), code):
            return step
    return None


# ───────────────────────── общие помощники ─────────────────────────

def _err(msg: str, status: int, **extra):
    return web.json_response({"error": msg, **extra}, status=status)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _get_account(conn, tg_id):
    return conn.execute("SELECT * FROM web_accounts WHERE tg_id = ?", (tg_id,)).fetchone()


def _register_failure(conn, row):
    """Считает неверную попытку; после MAX_ATTEMPTS блокирует вход. Возвращает True, если заблокирован."""
    failed = row["failed"] + 1
    locked = int(time.time()) + LOCK_SECONDS if failed >= MAX_ATTEMPTS else 0
    conn.execute(
        "UPDATE web_accounts SET failed = ?, locked_until = ? WHERE tg_id = ?",
        (0 if locked else failed, locked, row["tg_id"]),
    )
    conn.commit()
    return bool(locked)


def _locked_msg(row):
    now = int(time.time())
    if row["locked_until"] > now:
        return f"Слишком много попыток. Подождите {(row['locked_until'] - now) // 60 + 1} мин."
    return None


async def _auth(request):
    try:
        body = await request.json()
    except Exception:
        return None, None, _err("Неверный запрос", 400)
    user = validate_init_data(body.get("initData", ""))
    if not user:
        return None, None, _err("Откройте приложение через Telegram", 401)
    return user, body, None


async def _authed(request):
    """То же, что _auth, но требует ещё и токен сессии (т.е. пройденный вход по паролю)."""
    user, body, err = await _auth(request)
    if err:
        return None, None, err
    token = str(body.get("token", ""))
    with get_conn() as conn:
        row = conn.execute(
            "SELECT tg_id, expires FROM web_sessions WHERE token_hash = ?", (_hash_token(token),)
        ).fetchone()
    if not row or row["tg_id"] != user["id"] or row["expires"] < time.time():
        return None, None, _err("Сессия истекла, войдите заново", 401, relogin=True)
    return user, body, None


def _new_session(conn, tg_id) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM web_sessions WHERE expires < ?", (int(time.time()),))
    conn.execute(
        "INSERT INTO web_sessions (token_hash, tg_id, expires) VALUES (?, ?, ?)",
        (_hash_token(token), tg_id, int(time.time()) + SESSION_TTL),
    )
    return token


def _log_tx(user_id, amount, kind, title):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO transactions (tx_id, user_id, amount, kind, status, created_at, title) "
            "VALUES (?, ?, ?, ?, 'paid', ?, ?)",
            (str(uuid.uuid4()), user_id, amount, kind, int(time.time()), title),
        )
        conn.commit()


def _plan(plan_id):
    return next((p for p in config.CARD_PLANS if p["id"] == plan_id), None)


# ───────────────────────── регистрация и вход ─────────────────────────

async def status(request):
    user, _, err = await _auth(request)
    if err:
        return err
    with get_conn() as conn:
        row = _get_account(conn, user["id"])
    return web.json_response({"registered": bool(row), "name": _display_name(user)})


async def register(request):
    user, body, err = await _auth(request)
    if err:
        return err
    password = str(body.get("password", ""))
    if len(password) < MIN_PASSWORD:
        return _err(f"Пароль минимум {MIN_PASSWORD} символов", 400)
    salt = secrets.token_bytes(16)
    ph = hash_password(password, salt)
    with get_conn() as conn:
        if _get_account(conn, user["id"]):
            return _err("Аккаунт уже создан, войдите с паролем", 409)
        conn.execute(
            "INSERT INTO web_accounts (tg_id, name, pass_hash, salt, created_at) VALUES (?, ?, ?, ?, ?)",
            (user["id"], _display_name(user), ph, salt, int(time.time())),
        )
        token = _new_session(conn, user["id"])
        conn.commit()
    db.get_or_create_user(user["id"], user.get("username"))
    return web.json_response({"ok": True, "name": _display_name(user), "token": token})


async def login(request):
    user, body, err = await _auth(request)
    if err:
        return err
    password = str(body.get("password", ""))
    code = str(body.get("code", "")).strip()
    with get_conn() as conn:
        row = _get_account(conn, user["id"])
        if not row:
            return _err("Аккаунт не найден, зарегистрируйтесь", 404)
        msg = _locked_msg(row)
        if msg:
            return _err(msg, 429)
        if not hmac.compare_digest(hash_password(password, row["salt"]), row["pass_hash"]):
            _register_failure(conn, row)
            return _err("Неверный пароль", 401)
        last_step = row["totp_last"] or 0
        if row["totp_secret"]:
            if not code:
                return web.json_response({"need_code": True})
            step = verify_totp(row["totp_secret"], code, last_step)
            if step is None:
                _register_failure(conn, row)
                return _err("Неверный код из приложения", 401, need_code=True)
            last_step = step
        conn.execute(
            "UPDATE web_accounts SET failed = 0, locked_until = 0, totp_last = ? WHERE tg_id = ?",
            (last_step, user["id"]),
        )
        token = _new_session(conn, user["id"])
        conn.commit()
    db.get_or_create_user(user["id"], user.get("username"))
    return web.json_response({"ok": True, "name": row["name"], "token": token})


async def logout(request):
    user, body, err = await _auth(request)
    if err:
        return err
    with get_conn() as conn:
        conn.execute("DELETE FROM web_sessions WHERE token_hash = ?", (_hash_token(str(body.get("token", ""))),))
        conn.commit()
    return web.json_response({"ok": True})


# ───────────────────────── данные: баланс, карты, история ─────────────────────────

async def state(request):
    user, _, err = await _authed(request)
    if err:
        return err
    db.get_or_create_user(user["id"], user.get("username"))
    cards = []
    for c in db.get_user_cards(user["id"]):
        plan = _plan(c["plan_id"])
        cards.append({
            "plan": plan["title"] if plan else c["plan_id"],
            "plan_id": c["plan_id"],
            "pan": c["pan"],
            "exp": c["exp"],
            "status": c["status"],
        })
    with get_conn() as conn:
        ops = conn.execute(
            "SELECT tx_id, amount, kind, status, created_at, title FROM transactions "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 100",
            (user["id"],),
        ).fetchall()
        acc = _get_account(conn, user["id"])
    return web.json_response({
        "name": acc["name"] if acc else _display_name(user),
        "balance": db.get_balance(user["id"]),
        "cards": cards,
        "ops": [dict(o) for o in ops],
        "twofa": bool(acc and acc["totp_secret"]),
        "mock_payments": MOCK_PAYMENTS,
    })


async def topup(request):
    user, body, err = await _authed(request)
    if err:
        return err
    if not MOCK_PAYMENTS:
        return _err("Приём платежей ещё не подключён", 503)
    try:
        amount = int(body.get("amount"))
    except (TypeError, ValueError):
        return _err("Неверная сумма", 400)
    method = str(body.get("method", ""))
    if amount not in TOPUP_AMOUNTS or method not in METHODS:
        return _err("Неверные параметры оплаты", 400)
    db.get_or_create_user(user["id"], user.get("username"))
    db.add_balance(user["id"], amount)
    _log_tx(user["id"], amount, "topup", METHODS[method])
    return web.json_response({"ok": True, "balance": db.get_balance(user["id"])})


async def buy(request):
    user, body, err = await _authed(request)
    if err:
        return err
    plan = _plan(str(body.get("plan", "")))
    if not plan:
        return _err("Тариф не найден", 400)
    db.get_or_create_user(user["id"], user.get("username"))
    if not db.deduct_balance(user["id"], plan["price"]):
        return _err("Недостаточно средств", 402)
    try:
        card = get_card_provider().issue_card(user["id"], plan["id"])
    except Exception:
        db.add_balance(user["id"], plan["price"])  # возвращаем деньги, если выпуск не удался
        return _err("Не удалось выпустить карту, деньги возвращены", 502)
    card["user_id"], card["plan_id"] = user["id"], plan["id"]
    db.save_card(card)
    _log_tx(user["id"], plan["price"], "buy_card", plan["title"])
    return web.json_response({
        "ok": True,
        "balance": db.get_balance(user["id"]),
        "card": {"plan": plan["title"], "pan": card["pan"], "exp": card["exp"], "cvv": card.get("cvv")},
    })


# ───────────────────────── безопасность ─────────────────────────

async def change_password(request):
    user, body, err = await _authed(request)
    if err:
        return err
    old, new = str(body.get("old", "")), str(body.get("new", ""))
    if len(new) < MIN_PASSWORD:
        return _err(f"Новый пароль минимум {MIN_PASSWORD} символов", 400)
    with get_conn() as conn:
        row = _get_account(conn, user["id"])
        msg = _locked_msg(row)
        if msg:
            return _err(msg, 429)
        if not hmac.compare_digest(hash_password(old, row["salt"]), row["pass_hash"]):
            _register_failure(conn, row)
            return _err("Текущий пароль неверный", 401)
        salt = secrets.token_bytes(16)
        conn.execute(
            "UPDATE web_accounts SET pass_hash = ?, salt = ?, failed = 0, locked_until = 0 WHERE tg_id = ?",
            (hash_password(new, salt), salt, user["id"]),
        )
        # все остальные сессии закрываем, текущую оставляем
        conn.execute(
            "DELETE FROM web_sessions WHERE tg_id = ? AND token_hash != ?",
            (user["id"], _hash_token(str(body.get("token", "")))),
        )
        conn.commit()
    return web.json_response({"ok": True})


async def twofa_setup(request):
    user, _, err = await _authed(request)
    if err:
        return err
    with get_conn() as conn:
        row = _get_account(conn, user["id"])
        if row["totp_secret"]:
            return _err("Двухфакторная защита уже включена", 409)
        secret = base64.b32encode(os.urandom(20)).decode().rstrip("=")
        conn.execute("UPDATE web_accounts SET totp_pending = ? WHERE tg_id = ?", (secret, user["id"]))
        conn.commit()
    uri = f"otpauth://totp/PayGoodBot:{quote(row['name'])}?secret={secret}&issuer=PayGoodBot"
    return web.json_response({"secret": secret, "uri": uri})


async def twofa_enable(request):
    user, body, err = await _authed(request)
    if err:
        return err
    with get_conn() as conn:
        row = _get_account(conn, user["id"])
        msg = _locked_msg(row)
        if msg:
            return _err(msg, 429)
        if not row["totp_pending"]:
            return _err("Сначала запросите ключ", 400)
        step = verify_totp(row["totp_pending"], str(body.get("code", "")))
        if step is None:
            _register_failure(conn, row)
            return _err("Неверный код. Проверьте время на телефоне", 401)
        conn.execute(
            "UPDATE web_accounts SET totp_secret = totp_pending, totp_pending = NULL, totp_last = ? WHERE tg_id = ?",
            (step, user["id"]),
        )
        conn.commit()
    return web.json_response({"ok": True})


async def twofa_disable(request):
    user, body, err = await _authed(request)
    if err:
        return err
    with get_conn() as conn:
        row = _get_account(conn, user["id"])
        msg = _locked_msg(row)
        if msg:
            return _err(msg, 429)
        if not row["totp_secret"]:
            return _err("Двухфакторная защита не включена", 400)
        if not hmac.compare_digest(hash_password(str(body.get("password", "")), row["salt"]), row["pass_hash"]):
            _register_failure(conn, row)
            return _err("Неверный пароль", 401)
        step = verify_totp(row["totp_secret"], str(body.get("code", "")), row["totp_last"] or 0)
        if step is None:
            _register_failure(conn, row)
            return _err("Неверный код из приложения", 401)
        conn.execute(
            "UPDATE web_accounts SET totp_secret = NULL, totp_pending = NULL, totp_last = 0 WHERE tg_id = ?",
            (user["id"],),
        )
        conn.commit()
    return web.json_response({"ok": True})


# ───────────────────────── приложение ─────────────────────────

@web.middleware
async def cors(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


async def health(request):
    return web.json_response({"ok": True})


ROUTES = {
    "/api/status": status,
    "/api/register": register,
    "/api/login": login,
    "/api/logout": logout,
    "/api/state": state,
    "/api/topup": topup,
    "/api/buy": buy,
    "/api/change_password": change_password,
    "/api/2fa/setup": twofa_setup,
    "/api/2fa/enable": twofa_enable,
    "/api/2fa/disable": twofa_disable,
}


def create_app() -> web.Application:
    app = web.Application(middlewares=[cors])
    app.router.add_get("/", health)
    for path, handler in ROUTES.items():
        app.router.add_post(path, handler)
    app.router.add_route("OPTIONS", "/api/{tail:.*}", health)
    return app
