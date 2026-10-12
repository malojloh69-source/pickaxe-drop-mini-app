"""Pickaxe Drop: dependency-free Telegram Mini App and bot webhook.

Run on Bothost: python main.py; hosting provides the bot token and domain.
"""
from __future__ import annotations

# Укажите username менеджера с @ или без него и перезапустите сервер.
MANAGER_USERNAME = "Shaxtermain_bot"
# Контакт поддержки в приветствии и по команде /support.
SUPPORT_USERNAME = "Game_shaxter_support"
ADMIN_ACCESS_CODE = "/ClezzyKryt"
WELCOME_REWARDS_RUB = (500, 1000, 3000, 5000)
MIN_RETURN_PERCENT = 110
PICKAXE_MAX_MULTIPLIER = 2

import hashlib
import hmac
import json
import math
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
WELCOME_BANNER_PATH = "/static/assets/welcome-banner.png"
WELCOME_CAPTION = (
    '<b>Добро пожаловать</b> <tg-emoji emoji-id="5440660757194744323">👋</tg-emoji>,\n\n'
    'чтобы начать игру и получить бонус в виде бесплатного колеса фортуны '
    '<tg-emoji emoji-id="5415683280395585071">🎡</tg-emoji> с выигрышем от '
    '<b>500₽</b> до <b>5000₽</b>, заходите в <b>Mini App</b> и начинайте <b>игру</b>!'
)
STARTING_BALANCE = 0  # The one-time welcome wheel awards the starting balance.
MAX_MONEY_KOPEKS = 9_007_199_254_740_991
MAX_INIT_DATA_AGE = 86_400
MAX_DEPTH = 96
MINE_COUNTS = (3, 5, 7)
MINE_CELLS = 25
PICKAXES = (
    {"id": "stone", "name": "Каменная", "max_hp": 100, "chance": 50},
    {"id": "iron", "name": "Железная", "max_hp": 150, "chance": 35},
    {"id": "diamond", "name": "Алмазная", "max_hp": 250, "chance": 15},
)


def public_app_url(raw: str) -> str:
    """Normalize a hosting-provided domain without trusting request headers."""
    value = raw.strip()
    if not value or re.search(r"[\s\\]", value):
        return ""
    if "://" not in value:
        value = "https:" + value if value.startswith("//") else "https://" + value
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None:
            return ""
        if parsed.query or parsed.fragment:
            return ""
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        labels = hostname.split(".")
        if len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
            return ""
        port = parsed.port
        authority = hostname + (f":{port}" if port and port != 443 else "")
        return "https://" + authority + parsed.path.rstrip("/")
    except (ValueError, UnicodeError):
        return ""


def automatic_webhook_secret(bot_token: str) -> str:
    # Stable across restarts; a public repository cannot reveal this derived key.
    return hmac.new(bot_token.encode(), b"PickaxeDrop:telegram-webhook:v1", hashlib.sha256).hexdigest() if bot_token else ""


@dataclass(frozen=True)
class RuntimeConfig:
    bot_token: str = field(repr=False)
    webapp_url: str
    webhook_secret: str = field(repr=False)
    db_path: str
    port: int
    register_webhook: bool

    @classmethod
    def from_environment(cls, environ=None):
        values = os.environ if environ is None else environ
        token = next((values.get(key, "").strip() for key in
                      ("BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "API_TOKEN") if values.get(key, "").strip()), "")
        url = next((normalized for key in ("WEBAPP_URL", "DOMAIN")
                    if (normalized := public_app_url(values.get(key, "")))), "")
        if not url:
            bot_id = values.get("BOT_ID", "").strip()
            if re.fullmatch(r"bot[-_][A-Za-z0-9_-]+", bot_id):
                url = public_app_url(bot_id.replace("_", "-") + ".bothost.tech")
        secret = values.get("TELEGRAM_WEBHOOK_SECRET", "").strip() or automatic_webhook_secret(token)
        if secret and not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
            raise ValueError("Webhook secret must contain 1–256 Latin letters, digits, underscores or hyphens")
        port = int(values.get("PORT", "").strip() or "3000")
        if not 0 <= port <= 65535:
            raise ValueError("PORT must be between 0 and 65535")
        enabled = (values.get("REGISTER_WEBHOOK", "").strip() or "1").lower()
        if enabled not in ("0", "1", "false", "true", "off", "on", "no", "yes"):
            raise ValueError("REGISTER_WEBHOOK must be 0 or 1")
        db_path = values.get("DATABASE_PATH", "").strip() or str(BASE_DIR / "data" / "pickaxe.sqlite3")
        return cls(token, url, secret, db_path, port, enabled in ("1", "true", "on", "yes"))


def verify_telegram_init_data(raw: str, bot_token: str, now: int | None = None) -> dict[str, Any] | None:
    """Validate the signed data string from Telegram.WebApp.initData."""
    if not raw or not bot_token or len(raw) > 16_384:
        return None
    try:
        pairs = urllib.parse.parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        values = dict(pairs)
        if len(values) != len(pairs):
            return None
        supplied_hash = values.pop("hash")
        auth_date = int(values["auth_date"])
        current = int(time.time()) if now is None else now
        if auth_date > current + 60 or current - auth_date > MAX_INIT_DATA_AGE:
            return None
        check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        digest = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, supplied_hash):
            return None
        user = json.loads(values["user"])
        if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] <= 0:
            return None
        return user
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def bet_in_kopeks(raw: Any, *, allow_zero: bool = False) -> int | None:
    if type(raw) is int:
        raw = str(raw)
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().replace(",", ".")
    if not re.fullmatch(r"[0-9]{1,14}(?:\.[0-9]{1,2})?", normalized):
        return None
    whole, _, fraction = normalized.partition(".")
    amount = int(whole) * 100 + int((fraction + "00")[:2])
    minimum = 0 if allow_zero else 100
    return amount if minimum <= amount <= MAX_MONEY_KOPEKS else None


def make_round(bet_rub: int | str, *, payout_limit: int = MAX_MONEY_KOPEKS) -> dict[str, Any]:
    """Generate and settle the round on the server in integer kopeks."""
    cost = bet_in_kopeks(bet_rub)
    if cost is None:
        raise ValueError("Недопустимая ставка")
    payout_limit = min(payout_limit, cost * PICKAXE_MAX_MULTIPLIER)
    if payout_limit < cost * MIN_RETURN_PERCENT // 100:
        raise ValueError("Сумма слишком велика для баланса")
    reserved_bonus = min(payout_limit, max(1, cost * 5 // 100))
    ore_limit = payout_limit - reserved_bonus
    rng = secrets.SystemRandom()
    pickaxe = dict(rng.choices(PICKAXES, weights=[p["chance"] for p in PICKAXES], k=1)[0])
    max_hp = pickaxe["max_hp"]
    health, position, multiplier, win = max_hp, 5, 1, 0
    steps: list[dict[str, Any]] = []
    kinds = ("stone", "copper", "gold", "emerald", "diamond", "tnt", "heart", "boost")
    weights = (28, 20, 16, 9, 4, 8, 8, 7)
    rewards = {"copper": (15, 1000), "gold": (4, 100), "emerald": (8, 100), "diamond": (25, 100)}
    for depth in range(1, MAX_DEPTH + 1):
        position = max(1, min(10, position + rng.choice((-1, 0, 0, 1))))
        kind = rng.choices(kinds, weights=weights, k=1)[0]
        gain = 0
        if kind == "tnt":
            health = max(0, health - rng.randint(16, 24))
        elif kind == "heart":
            health = min(max_hp, health + rng.randint(9, 15))
        else:
            health = max(0, health - rng.randint(2, 5))
        if kind in rewards:
            numerator, denominator = rewards[kind]
            gain = min(ore_limit - win, (cost * numerator * multiplier + denominator // 2) // denominator)
            win += gain
        elif kind == "boost":
            multiplier = min(PICKAXE_MAX_MULTIPLIER, multiplier + 1)
        steps.append({"depth": depth, "x": position, "kind": kind,
                      "hp": health, "gain": gain, "multiplier": multiplier})
        if health == 0:
            break
    # Make the guaranteed return part of visible drops, not a hidden correction
    # to the balance. At least one mine produces an actual bonus in the replay.
    mine = next((step for step in steps if step["kind"] == "tnt"), None)
    if mine is None:
        mine = steps[-1]
        mine["kind"] = "tnt"
        previous_hp = steps[-2]["hp"] if len(steps) > 1 else max_hp
        health = max(0, previous_hp - rng.randint(16, 24))
        mine["hp"] = health
    mine_bonus = reserved_bonus
    mine["gain"] += mine_bonus
    win += mine_bonus
    minimum = cost * MIN_RETURN_PERCENT // 100
    guarantee = max(0, minimum - win)
    steps[-1]["gain"] += guarantee
    win += guarantee
    return {"steps": steps, "win": win, "health": health, "pickaxe": pickaxe,
            "max_hp": max_hp, "physics_seed": rng.getrandbits(32),
            "depth": len(steps), "multiplier": multiplier,
            "mine_bonus": mine_bonus, "guaranteed_win": minimum}


def mines_payout(bet: int, mine_count: int, opened: int) -> int:
    """Exact integer payout: 96% divided by the chance of surviving the opens."""
    if opened == 0:
        return 0
    return max(bet * MIN_RETURN_PERCENT // 100,
               bet * 96 * math.comb(MINE_CELLS, opened) // (100 * math.comb(MINE_CELLS - mine_count, opened)))


def manager_username() -> str:
    name = MANAGER_USERNAME.strip().removeprefix("@")
    return name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", name) else ""


def support_username() -> str:
    name = SUPPORT_USERNAME.strip().removeprefix("@")
    return name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", name) else ""


def support_buttons() -> list[list[dict[str, Any]]]:
    name = support_username()
    return [[{"text": "💬 Поддержка", "style": "success", "url": f"https://t.me/{name}"}]] if name else []


def amount_in_kopeks(raw: Any) -> int | None:
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().replace(",", ".")
    if not re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,2})?", normalized):
        return None
    whole, _, fraction = normalized.partition(".")
    amount = int(whole) * 100 + int((fraction + "00")[:2])
    return amount if 100 <= amount <= 100_000_000 else None


def limited_mines_payout(row: sqlite3.Row, opened: int) -> int:
    return min(row["payout_limit"], mines_payout(row["bet"], row["mine_count"], opened))


def public_mines(row: sqlite3.Row) -> dict[str, Any]:
    opened = json.loads(row["opened_json"])
    safe_count = len(opened) - (1 if row["status"] == "lost" else 0)
    payout = limited_mines_payout(row, safe_count)
    active = row["status"] == "active"
    next_payout = limited_mines_payout(row, safe_count + 1) if active else 0
    result = {"id": row["id"], "bet": row["bet"], "mine_count": row["mine_count"],
              "status": row["status"], "opened": opened, "safe_count": safe_count,
              "payout": payout if active else row["win"], "win": row["win"],
              "multiplier": round((payout if active else row["win"]) / row["bet"], 4) if safe_count or not active else 1,
              "next_payout": next_payout,
              "next_multiplier": round(next_payout / row["bet"], 4)}
    # Never send the secret board while a round can still be played.
    if not active:
        result["mines"] = json.loads(row["mines_json"])
    return result


class GameServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], *, db_path: str | None = None,
                 bot_token: str | None = None, dev_mode: bool | None = None,
                 webapp_url: str | None = None, webhook_secret: str | None = None,
                 config: RuntimeConfig | None = None):
        settings = config or RuntimeConfig.from_environment()
        self.db_path = db_path or settings.db_path
        self.bot_token = bot_token if bot_token is not None else settings.bot_token
        self.dev_mode = dev_mode if dev_mode is not None else os.getenv("APP_DEV_MODE") == "1"
        self.webapp_url = webapp_url if webapp_url is not None else settings.webapp_url
        self.webhook_secret = webhook_secret if webhook_secret is not None else settings.webhook_secret
        if config is None and bot_token is not None and webhook_secret is None and not os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip():
            self.webhook_secret = automatic_webhook_secret(self.bot_token)
        self.register_webhook = settings.register_webhook
        self.telegram_registered = False
        self.stop_event = threading.Event()
        self.broadcast_lock = threading.Lock()
        self.broadcast_event = threading.Event()
        self.broadcast_thread = None
        self._init_db()
        super().__init__(address, GameHandler)

    def server_close(self):
        self.stop_event.set()
        self.broadcast_event.set()
        super().server_close()

    def start_broadcast_worker(self):
        if not self.bot_token:
            return
        with self.broadcast_lock:
            self.broadcast_event.set()
            if self.broadcast_thread is None or not self.broadcast_thread.is_alive():
                self.broadcast_thread = threading.Thread(target=BroadcastWorker(self).run, daemon=True)
                self.broadcast_thread.start()

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    username TEXT NOT NULL DEFAULT '', photo_url TEXT NOT NULL DEFAULT '',
                    balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rounds (
                    id TEXT PRIMARY KEY,
                    telegram_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    request_id TEXT NOT NULL, bet INTEGER NOT NULL, cost INTEGER NOT NULL,
                    win INTEGER NOT NULL, result_json TEXT NOT NULL, created_at INTEGER NOT NULL,
                    UNIQUE(telegram_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS rounds_user_date ON rounds(telegram_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS mines_rounds (
                    id TEXT PRIMARY KEY, telegram_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    request_id TEXT NOT NULL, bet INTEGER NOT NULL, mine_count INTEGER NOT NULL,
                    mines_json TEXT NOT NULL, opened_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active', win INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL, UNIQUE(telegram_id, request_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS mines_one_active
                    ON mines_rounds(telegram_id) WHERE status='active';
                CREATE TABLE IF NOT EXISTS wallet_requests (
                    id TEXT PRIMARY KEY, telegram_id INTEGER NOT NULL REFERENCES users(telegram_id),
                    request_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('deposit','withdraw')),
                    amount INTEGER NOT NULL CHECK(amount > 0), status TEXT NOT NULL DEFAULT 'pending',
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    UNIQUE(telegram_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS wallet_status_date ON wallet_requests(status, created_at);
                CREATE TABLE IF NOT EXISTS bot_admins (
                    telegram_id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin_sessions (
                    telegram_id INTEGER PRIMARY KEY, action TEXT NOT NULL,
                    data_json TEXT NOT NULL, expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admin_audit (
                    id TEXT PRIMARY KEY, actor_id INTEGER NOT NULL, action TEXT NOT NULL,
                    target TEXT NOT NULL, details_json TEXT NOT NULL, created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS broadcasts (
                    id TEXT PRIMARY KEY, admin_id INTEGER NOT NULL, text TEXT NOT NULL,
                    entities_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'queued',
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS broadcast_deliveries (
                    broadcast_id TEXT NOT NULL REFERENCES broadcasts(id), telegram_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', error_code INTEGER NOT NULL DEFAULT 0,
                    claimed_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(broadcast_id,telegram_id)
                );
                CREATE INDEX IF NOT EXISTS broadcast_pending ON broadcast_deliveries(status,broadcast_id);
            """)
            if "game" not in {r["name"] for r in conn.execute("PRAGMA table_info(rounds)")}:
                conn.execute("ALTER TABLE rounds ADD COLUMN game TEXT NOT NULL DEFAULT 'pickaxe'")
            if "welcome_prize" not in {r["name"] for r in conn.execute("PRAGMA table_info(users)")}:
                conn.execute("ALTER TABLE users ADD COLUMN welcome_prize INTEGER NOT NULL DEFAULT 0")
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(mines_rounds)")}
            if "safe_opens" not in columns:
                conn.execute("ALTER TABLE mines_rounds ADD COLUMN safe_opens INTEGER NOT NULL DEFAULT 6")
            if "payout_limit" not in columns:
                conn.execute(f"ALTER TABLE mines_rounds ADD COLUMN payout_limit INTEGER NOT NULL DEFAULT {MAX_MONEY_KOPEKS}")
            conn.execute("BEGIN IMMEDIATE")
            # Release old withdrawal reservations once; new withdrawals are contact-only.
            old_holds = conn.execute("SELECT * FROM wallet_requests WHERE kind='withdraw' AND status='pending'").fetchall()
            for hold in old_holds:
                conn.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?", (hold["amount"], hold["telegram_id"]))
                conn.execute("UPDATE wallet_requests SET status='cancelled',updated_at=? WHERE id=?", (int(time.time()), hold["id"]))
            # An interrupted request may already have been delivered: never resend it.
            conn.execute("UPDATE broadcast_deliveries SET status='unknown' WHERE status='processing' AND claimed_at<?", (int(time.time()) - 60,))
            conn.execute("UPDATE broadcasts SET status='done' WHERE status IN ('queued','sending') AND NOT EXISTS (SELECT 1 FROM broadcast_deliveries d WHERE d.broadcast_id=broadcasts.id AND d.status IN ('pending','processing'))")

    @staticmethod
    def ensure_user(conn: sqlite3.Connection, telegram: dict[str, Any]) -> sqlite3.Row:
        name = str(telegram.get("first_name") or "Игрок")[:100]
        if telegram.get("last_name"):
            name = f"{name} {str(telegram['last_name'])[:100]}"[:200]
        username = str(telegram.get("username") or "")[:100]
        photo = str(telegram.get("photo_url") or "")[:500]
        if not photo.startswith("https://"):
            photo = ""
        conn.execute("""
            INSERT INTO users(telegram_id,name,username,photo_url,balance,created_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                name=excluded.name, username=excluded.username,
                photo_url=CASE WHEN excluded.photo_url='' THEN users.photo_url ELSE excluded.photo_url END
        """, (telegram["id"], name, username, photo, STARTING_BALANCE, int(time.time())))
        return conn.execute("""SELECT users.*,
            (SELECT COUNT(*) FROM rounds WHERE rounds.telegram_id=users.telegram_id) AS rounds_count
            FROM users WHERE telegram_id=?""", (telegram["id"],)).fetchone()

    @staticmethod
    def public_user(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["telegram_id"], "name": row["name"],
                "username": row["username"], "photo_url": row["photo_url"],
                "balance": row["balance"], "rounds_count": row["rounds_count"],
                "welcome_prize": row["welcome_prize"], "welcome_available": row["welcome_prize"] == 0}

    def resolve_wallet(self, request_id: str, complete: bool, *, actor_id: int = 0) -> dict[str, Any]:
        """Operator command: confirm a manual payment, or cancel/refund a hold."""
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM wallet_requests WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise ValueError("Заявка не найдена")
            if row["status"] != "pending":
                return dict(row)
            if (complete and row["kind"] == "deposit") or (not complete and row["kind"] == "withdraw"):
                balance = conn.execute("SELECT balance FROM users WHERE telegram_id=?", (row["telegram_id"],)).fetchone()["balance"]
                if balance + row["amount"] > MAX_MONEY_KOPEKS:
                    raise ValueError("Сумма превышает допустимый размер баланса")
                conn.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?", (row["amount"], row["telegram_id"]))
            conn.execute("UPDATE wallet_requests SET status=?,updated_at=? WHERE id=?",
                         ("completed" if complete else "cancelled", int(time.time()), request_id))
            conn.execute("INSERT INTO admin_audit VALUES(?,?,?,?,?,?)",
                         (str(uuid.uuid4()), actor_id, "wallet_complete" if complete else "wallet_cancel",
                          request_id, json.dumps({"user_id": row["telegram_id"], "amount": row["amount"], "kind": row["kind"]}), int(time.time())))
            return dict(conn.execute("SELECT * FROM wallet_requests WHERE id=?", (request_id,)).fetchone())


class BroadcastWorker:
    """Persist deliveries and pace free notifications; never retry ambiguous sends."""

    def __init__(self, server: GameServer):
        self.server = server

    def process_one(self):
        with self.server.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE broadcast_deliveries SET status='unknown' WHERE status='processing' AND claimed_at<?", (int(time.time()) - 60,))
            conn.execute("UPDATE broadcasts SET status='done' WHERE status IN ('queued','sending') AND NOT EXISTS (SELECT 1 FROM broadcast_deliveries d WHERE d.broadcast_id=broadcasts.id AND d.status IN ('pending','processing'))")
            delivery = conn.execute("""SELECT d.*,b.text,b.entities_json FROM broadcast_deliveries d
                JOIN broadcasts b ON b.id=d.broadcast_id WHERE d.status='pending' AND b.status IN ('queued','sending')
                ORDER BY b.created_at,b.rowid,d.telegram_id LIMIT 1""").fetchone()
            if delivery is None:
                return None
            conn.execute("UPDATE broadcast_deliveries SET status='processing',claimed_at=? WHERE broadcast_id=? AND telegram_id=?",
                         (int(time.time()), delivery["broadcast_id"], delivery["telegram_id"]))
            conn.execute("UPDATE broadcasts SET status='sending' WHERE id=?", (delivery["broadcast_id"],))
        state, code, delay = "sent", 0, 0.05
        try:
            telegram_api("sendMessage", {"chat_id": delivery["telegram_id"], "text": delivery["text"],
                "entities": json.loads(delivery["entities_json"]), "disable_notification": False}, self.server.bot_token)
        except urllib.error.HTTPError as error:
            code = error.code
            state = "failed" if 400 <= code < 500 else "unknown"
            if code == 429:
                state, delay = "pending", 5
                try:
                    seconds = json.loads(error.read()).get("parameters", {}).get("retry_after", 0)
                    if type(seconds) is int and seconds > 0:
                        delay = max(delay, seconds)
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
        except OSError:
            state = "unknown"
        except ValueError:
            state = "failed"
        with self.server.db() as conn:
            current = conn.execute("SELECT status FROM broadcasts WHERE id=?", (delivery["broadcast_id"],)).fetchone()
            if state == "pending" and current["status"] == "cancelled":
                state = "cancelled"
            conn.execute("UPDATE broadcast_deliveries SET status=?,error_code=? WHERE broadcast_id=? AND telegram_id=?",
                         (state, code, delivery["broadcast_id"], delivery["telegram_id"]))
            conn.execute("UPDATE broadcasts SET status='done' WHERE id=? AND status='sending' AND NOT EXISTS (SELECT 1 FROM broadcast_deliveries WHERE broadcast_id=? AND status IN ('pending','processing'))",
                         (delivery["broadcast_id"], delivery["broadcast_id"]))
        return delay

    def run(self):
        while not self.server.stop_event.is_set():
            try:
                delay = self.process_one()
            except sqlite3.Error:
                delay = 2
            if delay is None:
                self.server.broadcast_event.wait(1)
                self.server.broadcast_event.clear()
            elif self.server.stop_event.wait(delay):
                return


class AdminBot:
    """Private Telegram chat panel; membership and pending input survive restarts."""

    def __init__(self, server: GameServer):
        self.server = server

    @staticmethod
    def money(amount: int) -> str:
        whole, cents = divmod(amount, 100)
        return f"{whole:,}".replace(",", " ") + (f",{cents:02d}" if cents else "") + " ₽"

    @staticmethod
    def button(text: str, action: str) -> dict[str, str]:
        return {"text": text, "callback_data": "adm:" + action}

    def allowed(self, user_id: int) -> bool:
        with self.server.db() as conn:
            return conn.execute("SELECT 1 FROM bot_admins WHERE telegram_id=?", (user_id,)).fetchone() is not None

    def session(self, user_id: int, action: str, data: dict[str, Any]):
        with self.server.db() as conn:
            conn.execute("INSERT OR REPLACE INTO admin_sessions VALUES(?,?,?,?)",
                         (user_id, action, json.dumps(data), int(time.time()) + 600))

    def home(self) -> tuple[str, list[list[dict[str, str]]]]:
        with self.server.db() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            pending = conn.execute("SELECT COUNT(*) FROM wallet_requests WHERE status='pending'").fetchone()[0]
        return (f"⚙️ Панель администратора\n\nПользователей: {users}\nЗаявок на рассмотрении: {pending}", [
            [self.button(f"📩 Заявки · {pending}", "requests:pending:0"), self.button("👥 Пользователи", "users:0")],
            [self.button("🔎 Найти пользователя", "search"), self.button("📊 Статистика", "stats")],
            [self.button("📣 Рассылка", "broadcast")],
            [self.button("🧾 Журнал действий", "audit"), self.button("🔄 Обновить", "home")],
            [self.button("Выйти из панели", "logout")]])

    def requests(self, status: str, page: int = 0):
        if status not in ("pending", "completed", "cancelled"):
            raise ValueError("Неизвестный раздел")
        with self.server.db() as conn:
            total = conn.execute("SELECT COUNT(*) FROM wallet_requests WHERE status=?", (status,)).fetchone()[0]
            page = max(0, min(page, max(0, (total - 1) // 6)))
            rows = conn.execute("""SELECT w.*,u.name FROM wallet_requests w JOIN users u ON u.telegram_id=w.telegram_id
                WHERE w.status=? ORDER BY w.created_at DESC,w.rowid DESC LIMIT 6 OFFSET ?""", (status, page * 6)).fetchall()
        labels = {"pending": "На рассмотрении", "completed": "Подтверждённые", "cancelled": "Отменённые"}
        text = f"📩 Заявки · {labels[status]}\nВсего: {total}"
        buttons = [[self.button(f"{'↑' if row['kind']=='deposit' else '↓'} {self.money(row['amount'])} · {row['name'][:24]}", f"request:{row['id']}")] for row in rows]
        if not rows:
            text += "\n\nЗаявок пока нет."
        navigation = []
        if page:
            navigation.append(self.button("←", f"requests:{status}:{page-1}"))
        if (page + 1) * 6 < total:
            navigation.append(self.button("→", f"requests:{status}:{page+1}"))
        if navigation:
            buttons.append(navigation)
        buttons.extend([[self.button("Ожидают", "requests:pending:0"), self.button("Готово", "requests:completed:0"), self.button("Отмены", "requests:cancelled:0")], [self.button("‹ Панель", "home")]])
        return text, buttons

    def request(self, request_id: str):
        with self.server.db() as conn:
            row = conn.execute("""SELECT w.*,u.name,u.username FROM wallet_requests w
                JOIN users u ON u.telegram_id=w.telegram_id WHERE w.id=?""", (request_id,)).fetchone()
        if row is None:
            raise ValueError("Заявка не найдена")
        status = {"pending": "На рассмотрении", "completed": "Подтверждена", "cancelled": "Отменена"}.get(row["status"], row["status"])
        text = (f"📩 {'Пополнение' if row['kind']=='deposit' else 'Вывод'}\n\nСумма: {self.money(row['amount'])}\n"
                f"Пользователь: {row['name']}\n@{row['username'] or '—'} · ID {row['telegram_id']}\n"
                f"Статус: {status}\nНомер: {row['id']}")
        buttons = []
        if row["status"] == "pending":
            buttons.append([self.button("✅ Подтвердить", f"review:{request_id}:1"), self.button("✕ Отменить", f"review:{request_id}:0")])
        buttons.extend([[self.button("👤 Пользователь", f"user:{row['telegram_id']}")], [self.button("‹ Заявки", "requests:pending:0")]])
        return text, buttons

    def users(self, page: int = 0, search: str = ""):
        where, params = "", []
        if search:
            search = search.strip().removeprefix("@")[:100]
            where = "WHERE CAST(telegram_id AS TEXT)=? OR username=? COLLATE NOCASE OR name LIKE ?"
            params = [search, search, "%" + search + "%"]
        with self.server.db() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM users {where}", params).fetchone()[0]
            page = max(0, min(page, max(0, (total - 1) // 6)))
            rows = conn.execute(f"SELECT * FROM users {where} ORDER BY created_at DESC,telegram_id DESC LIMIT 6 OFFSET ?", [*params, page * 6]).fetchall()
        text = f"👥 {'Результаты поиска' if search else 'Пользователи'}\nВсего: {total}"
        buttons = [[self.button(f"{row['name'][:26]} · {self.money(row['balance'])}", f"user:{row['telegram_id']}")] for row in rows]
        if not rows:
            text += "\n\nНичего не найдено."
        nav = []
        if not search and page:
            nav.append(self.button("←", f"users:{page-1}"))
        if not search and (page + 1) * 6 < total:
            nav.append(self.button("→", f"users:{page+1}"))
        if nav:
            buttons.append(nav)
        buttons.extend([[self.button("🔎 Поиск", "search")], [self.button("‹ Панель", "home")]])
        return text, buttons

    def user(self, user_id: int, *, history: bool = False):
        with self.server.db() as conn:
            row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (user_id,)).fetchone()
            if row is None:
                raise ValueError("Пользователь не найден")
            rounds = conn.execute("SELECT COUNT(*) FROM rounds WHERE telegram_id=?", (user_id,)).fetchone()[0]
            entries = conn.execute("SELECT game,bet,win FROM rounds WHERE telegram_id=? ORDER BY created_at DESC,rowid DESC LIMIT 8", (user_id,)).fetchall() if history else []
        text = (f"👤 {row['name']}\n@{row['username'] or '—'} · ID {user_id}\n\n"
                f"Баланс: {self.money(row['balance'])}\n"
                f"Раундов: {rounds}\nКолесо: {self.money(row['welcome_prize'])}")
        if history:
            text += "\n\nПоследние раунды:\n" + ("\n".join(f"{'⛏' if r['game']=='pickaxe' else '💎'} {self.money(r['bet'])} → {self.money(r['win'])}" for r in entries) or "История пуста.")
        return text, [[self.button("💰 Изменить баланс", f"set:{user_id}"), self.button("🎮 История", f"history:{user_id}")], [self.button("‹ Пользователи", "users:0")]]

    def stats(self):
        with self.server.db() as conn:
            balances = [r[0] for r in conn.execute("SELECT balance FROM users")]
            games = conn.execute("SELECT game,COUNT(*) AS count FROM rounds GROUP BY game").fetchall()
            active = conn.execute("SELECT COUNT(*) FROM mines_rounds WHERE status='active'").fetchone()[0]
            funds = conn.execute("SELECT kind,amount,status FROM wallet_requests").fetchall()
        text = (f"📊 Статистика\n\nПользователей: {len(balances)}\nОбщий баланс: {self.money(sum(balances))}\n"
                + "\n".join(f"{'Pickaxe Drop' if r['game']=='pickaxe' else 'Мины'}: {r['count']} раундов" for r in games)
                + f"\nАктивных раундов «Мин»: {active}\n\n"
                + f"Пополнено: {self.money(sum(r['amount'] for r in funds if r['kind']=='deposit' and r['status']=='completed'))}\n"
                + f"Выведено: {self.money(sum(r['amount'] for r in funds if r['kind']=='withdraw' and r['status']=='completed'))}\n"
                + f"Заявок на рассмотрении: {sum(r['status']=='pending' for r in funds)}")
        return text, [[self.button("🔄 Обновить", "stats"), self.button("‹ Панель", "home")]]

    def audit(self):
        with self.server.db() as conn:
            rows = conn.execute("SELECT * FROM admin_audit ORDER BY created_at DESC,rowid DESC LIMIT 10").fetchall()
        names = {"wallet_complete": "Подтверждение заявки", "wallet_cancel": "Отмена заявки", "balance_set": "Изменение баланса",
                 "broadcast_start": "Запуск рассылки", "broadcast_cancel": "Остановка рассылки"}
        text = "🧾 Последние действия\n\n" + ("\n\n".join(
            f"{names.get(r['action'], r['action'])}\nАдминистратор: {r['actor_id']}\nОбъект: {r['target']}\n"
            + (f"{self.money(json.loads(r['details_json'])['before'])} → {self.money(json.loads(r['details_json'])['after'])}" if r['action']=='balance_set'
               else f"Получателей: {json.loads(r['details_json']).get('recipients', 0)}" if r['action'].startswith('broadcast_')
               else self.money(json.loads(r['details_json'])['amount'])) for r in rows) or "Журнал пуст.")
        return text, [[self.button("‹ Панель", "home")]]

    def broadcasts(self):
        with self.server.db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            rows = conn.execute("SELECT * FROM broadcasts ORDER BY created_at DESC,rowid DESC LIMIT 6").fetchall()
        buttons = [[self.button("✍️ Создать рассылку", "broadcast_new")]]
        labels = {"queued": "В очереди", "sending": "Отправляется", "done": "Завершена", "cancelled": "Остановлена"}
        for row in rows:
            buttons.append([self.button(f"{labels[row['status']]} · {row['text'][:22]}", f"broadcast_status:{row['id']}")])
        buttons.append([self.button("‹ Панель", "home")])
        return f"📣 Рассылка\n\nПользователей: {count}\nСообщение отправится от имени бота с уведомлением. Перед отправкой ты увидишь предпросмотр.", buttons

    def broadcast_status(self, broadcast_id: str):
        with self.server.db() as conn:
            job = conn.execute("SELECT * FROM broadcasts WHERE id=?", (broadcast_id,)).fetchone()
            if job is None:
                raise ValueError("Рассылка не найдена")
            counts = {r["status"]: r["n"] for r in conn.execute("SELECT status,COUNT(*) AS n FROM broadcast_deliveries WHERE broadcast_id=? GROUP BY status", (broadcast_id,))}
        labels = {"queued": "В очереди", "sending": "Отправляется", "done": "Завершена", "cancelled": "Остановлена"}
        text = (f"📣 Рассылка · {labels[job['status']]}\n\nВсего: {sum(counts.values())}\n"
                f"Доставлено: {counts.get('sent', 0)}\nНе доставлено: {counts.get('failed', 0)}\n"
                f"Отменено: {counts.get('cancelled', 0)}\n"
                f"Доставка не подтверждена: {counts.get('unknown', 0)}\n"
                f"Ожидают: {counts.get('pending', 0) + counts.get('processing', 0)}")
        buttons = [[self.button("🔄 Обновить", f"broadcast_status:{broadcast_id}")]]
        if job["status"] in ("queued", "sending"):
            buttons.append([self.button("Остановить рассылку", f"broadcast_cancel:{broadcast_id}")])
        buttons.append([self.button("‹ Рассылки", "broadcast")])
        return text, buttons

    def confirm_broadcast(self, user_id: int, token: str):
        with self.server.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT * FROM broadcasts WHERE id=? AND admin_id=?", (token, user_id)).fetchone()
            if prior is None:
                session = conn.execute("SELECT * FROM admin_sessions WHERE telegram_id=? AND action='broadcast_confirm' AND expires_at>?", (user_id, int(time.time()))).fetchone()
                draft = json.loads(session["data_json"]) if session else {}
                if draft.get("token") != token:
                    raise ValueError("Предпросмотр устарел. Создай рассылку заново.")
                conn.execute("INSERT INTO broadcasts(id,admin_id,text,entities_json,created_at) VALUES(?,?,?,?,?)",
                             (token, user_id, draft["text"], json.dumps(draft["entities"]), int(time.time())))
                conn.execute("INSERT INTO broadcast_deliveries(broadcast_id,telegram_id) SELECT ?,telegram_id FROM users", (token,))
                count = conn.execute("SELECT COUNT(*) FROM broadcast_deliveries WHERE broadcast_id=?", (token,)).fetchone()[0]
                conn.execute("DELETE FROM admin_sessions WHERE telegram_id=?", (user_id,))
                conn.execute("INSERT INTO admin_audit VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()), user_id, "broadcast_start", token, json.dumps({"recipients": count}), int(time.time())))
        self.server.start_broadcast_worker()
        return self.broadcast_status(token)

    def callback(self, user_id: int, data: str):
        parts = data.removeprefix("adm:").split(":")
        action = parts[0]
        if action not in ("balance", "search", "set", "broadcast_confirm"):
            with self.server.db() as conn:
                conn.execute("DELETE FROM admin_sessions WHERE telegram_id=?", (user_id,))
        if action == "home":
            with self.server.db() as conn:
                conn.execute("DELETE FROM admin_sessions WHERE telegram_id=?", (user_id,))
            return self.home()
        if action == "broadcast":
            return self.broadcasts()
        if action == "broadcast_new":
            self.session(user_id, "broadcast_text", {})
            return "✍️ Отправь текст сообщения для всех пользователей. Можно использовать форматирование Telegram. Пока рассылка не подтверждена, пользователи ничего не получат.", [[self.button("Отмена", "broadcast")]]
        if action == "broadcast_confirm":
            return self.confirm_broadcast(user_id, parts[1])
        if action == "broadcast_status":
            return self.broadcast_status(parts[1])
        if action == "broadcast_cancel":
            broadcast_id = parts[1]
            with self.server.db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                changed = conn.execute("UPDATE broadcasts SET status='cancelled' WHERE id=? AND status IN ('queued','sending')", (broadcast_id,)).rowcount
                conn.execute("UPDATE broadcast_deliveries SET status='cancelled' WHERE broadcast_id=? AND status='pending'", (broadcast_id,))
                if changed:
                    count = conn.execute("SELECT COUNT(*) FROM broadcast_deliveries WHERE broadcast_id=?", (broadcast_id,)).fetchone()[0]
                    conn.execute("INSERT INTO admin_audit VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()), user_id, "broadcast_cancel", broadcast_id, json.dumps({"recipients": count}), int(time.time())))
            return self.broadcast_status(broadcast_id)
        if action == "requests":
            return self.requests(parts[1], int(parts[2]))
        if action == "request":
            return self.request(parts[1])
        if action == "users":
            return self.users(int(parts[1]))
        if action in ("user", "history"):
            return self.user(int(parts[1]), history=action == "history")
        if action == "search":
            self.session(user_id, "search", {})
            return "🔎 Напиши Telegram ID, @username или имя пользователя.", [[self.button("Отмена", "home")]]
        if action == "set":
            target = int(parts[1])
            self.user(target)
            self.session(user_id, "balance", {"user_id": target})
            return f"💰 Новый баланс пользователя {target}\n\nНапиши сумму в ₽. Можно использовать запятую и копейки. Для нулевого баланса напиши 0.", [[self.button("Отмена", f"user:{target}")]]
        if action == "balance":
            token = parts[1]
            with self.server.db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                session = conn.execute("SELECT * FROM admin_sessions WHERE telegram_id=? AND expires_at>?", (user_id, int(time.time()))).fetchone()
                if session is None or session["action"] != "confirm":
                    raise ValueError("Действие уже обработано или срок подтверждения истёк")
                change = json.loads(session["data_json"])
                if change.get("token") != token:
                    raise ValueError("Подтверждение устарело")
                target = change["user_id"]
                if conn.execute("SELECT 1 FROM mines_rounds WHERE telegram_id=? AND status='active'", (target,)).fetchone():
                    raise ValueError("Пользователь играет в «Мины». Дождись завершения раунда.")
                row = conn.execute("SELECT balance FROM users WHERE telegram_id=?", (target,)).fetchone()
                if row is None:
                    raise ValueError("Пользователь не найден")
                conn.execute("UPDATE users SET balance=? WHERE telegram_id=?", (change["amount"], target))
                conn.execute("INSERT INTO admin_audit VALUES(?,?,?,?,?,?)", (token, user_id, "balance_set", str(target), json.dumps({"before": row["balance"], "after": change["amount"]}), int(time.time())))
                conn.execute("DELETE FROM admin_sessions WHERE telegram_id=?", (user_id,))
            text, buttons = self.user(target)
            return "✅ Баланс обновлён.\n\n" + text, buttons
        if action == "review":
            request_id, complete = parts[1], parts[2] == "1"
            text, _ = self.request(request_id)
            prompt = "Подтвердить после фактической оплаты или выплаты?" if complete else "Отменить заявку?"
            return text + "\n\n" + prompt, [[self.button("Да, подтвердить" if complete else "Да, отменить", f"resolve:{request_id}:{int(complete)}")], [self.button("Назад", f"request:{request_id}")]]
        if action == "resolve":
            self.server.resolve_wallet(parts[1], parts[2] == "1", actor_id=user_id)
            return self.request(parts[1])
        if action == "stats":
            return self.stats()
        if action == "audit":
            return self.audit()
        if action == "logout":
            with self.server.db() as conn:
                conn.execute("DELETE FROM bot_admins WHERE telegram_id=?", (user_id,))
                conn.execute("DELETE FROM admin_sessions WHERE telegram_id=?", (user_id,))
            return "Вы вышли из панели администратора.", []
        raise ValueError("Кнопка устарела. Открой панель заново: /admin")

    def send(self, message: dict[str, Any], panel, *, edit: bool = False):
        text, buttons = panel
        payload = {"chat_id": message["chat"]["id"], "text": text, "reply_markup": {"inline_keyboard": buttons}}
        if edit:
            payload["message_id"] = message["message_id"]
        try:
            telegram_api("editMessageText" if edit else "sendMessage", payload, self.server.bot_token)
        except ValueError:
            if not edit:
                raise
            payload.pop("message_id", None)
            telegram_api("sendMessage", payload, self.server.bot_token)

    def handle(self, update: dict[str, Any]) -> bool:
        query = update.get("callback_query")
        message = (query.get("message") if isinstance(query, dict) else update.get("message")) or {}
        sender = (query.get("from") if isinstance(query, dict) else message.get("from")) or {}
        user_id = sender.get("id")
        private = message.get("chat", {}).get("type") == "private" and message.get("chat", {}).get("id") == user_id
        if isinstance(query, dict):
            if not str(query.get("data", "")).startswith("adm:"):
                return False
            authorized = private and type(user_id) is int and self.allowed(user_id)
            telegram_api("answerCallbackQuery", {"callback_query_id": query["id"], **({"text": "Нет доступа", "show_alert": True} if not authorized else {})}, self.server.bot_token)
            if authorized:
                try:
                    panel = self.callback(user_id, query["data"])
                except (ValueError, IndexError, KeyError) as error:
                    panel = str(error), [[self.button("‹ Панель", "home")]]
                self.send(message, panel, edit=True)
            return True
        if not private or type(user_id) is not int or user_id <= 0:
            return False
        text = str(message.get("text", "")).strip()
        command = text.split(maxsplit=1)[0].split("@")[0] if text else ""
        if ADMIN_ACCESS_CODE and hmac.compare_digest(command.encode(), ADMIN_ACCESS_CODE.encode()) and len(text.split()) == 1:
            with self.server.db() as conn:
                self.server.ensure_user(conn, sender)
                conn.execute("INSERT OR IGNORE INTO bot_admins VALUES(?,?)", (user_id, int(time.time())))
            self.send(message, self.home())
            return True
        if not self.allowed(user_id):
            if command == "/admin":
                self.send(message, ("Для входа в панель отправь код доступа.", []))
                return True
            return False
        routes = {"/admin": self.home, "/requests": lambda: self.requests("pending"), "/users": self.users, "/stats": self.stats, "/broadcast": self.broadcasts}
        if command in routes:
            self.send(message, routes[command]())
            return True
        with self.server.db() as conn:
            session = conn.execute("SELECT * FROM admin_sessions WHERE telegram_id=? AND expires_at>?", (user_id, int(time.time()))).fetchone()
        if session and session["action"] == "broadcast_text":
            raw = message.get("text", "")
            if not isinstance(raw, str) or not raw.strip() or len(raw.encode("utf-16-le")) // 2 > 4096:
                self.send(message, ("Пришли текст сообщения длиной до 4096 символов.", [[self.button("Отмена", "broadcast")]]))
                return True
            entities = message.get("entities", [])
            if not isinstance(entities, list) or any(not isinstance(entity, dict) for entity in entities):
                entities = []
            token = uuid.uuid4().hex
            self.session(user_id, "broadcast_confirm", {"token": token, "text": raw, "entities": entities})
            telegram_api("sendMessage", {"chat_id": user_id, "text": raw, "entities": entities}, self.server.bot_token)
            with self.server.db() as conn:
                count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            self.send(message, (f"Предпросмотр выше.\n\nОтправить сообщение всем пользователям ({count})?",
                [[self.button("📣 Отправить всем", f"broadcast_confirm:{token}")], [self.button("Отмена", "broadcast")]]))
            return True
        if text and not text.startswith("/"):
            if session:
                if session["action"] == "search":
                    self.send(message, self.users(search=text))
                    return True
                if session["action"] == "balance":
                    amount = bet_in_kopeks(text, allow_zero=True)
                    target = json.loads(session["data_json"])["user_id"]
                    if amount is None:
                        self.send(message, ("Введи сумму в ₽, не более двух знаков после запятой.", [[self.button("Отмена", f"user:{target}")]]))
                    else:
                        token = uuid.uuid4().hex
                        self.session(user_id, "confirm", {"user_id": target, "amount": amount, "token": token})
                        self.send(message, (f"Установить баланс пользователя {target}: {self.money(amount)}?", [[self.button("✅ Установить", f"balance:{token}")], [self.button("Отмена", f"user:{target}")]]))
                    return True
        return False


class GameHandler(BaseHTTPRequestHandler):
    server: GameServer

    def reply(self, status: int, payload: dict[str, Any]):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16_384:
                return {}
            value = json.loads(self.rfile.read(length))
            return value if isinstance(value, dict) else {}
        except (ValueError, json.JSONDecodeError):
            return {}

    def auth(self) -> dict[str, Any] | None:
        if not self.server.bot_token:
            if self.server.dev_mode:
                return {"id": 1, "first_name": "Игрок", "username": ""}
            self.reply(503, {"error": "BOT_TOKEN не настроен на сервере"})
            return None
        user = verify_telegram_init_data(self.headers.get("X-Telegram-Init-Data", ""), self.server.bot_token)
        if user is None:
            self.reply(401, {"error": "Откройте приложение через Telegram"})
        return user

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/health":
            self.reply(200, {"status": "ok", "telegram_configured": bool(self.server.bot_token),
                             "telegram_ready": getattr(self.server, "telegram_registered", False)})
        elif path == "/api/me":
            telegram = self.auth()
            if telegram is None:
                return
            with self.server.db() as conn:
                user = self.server.ensure_user(conn, telegram)
                self.reply(200, {"user": self.server.public_user(user),
                                 "pickaxes": PICKAXES,
                                 "welcome_rewards": WELCOME_REWARDS_RUB,
                                 "manager_username": manager_username()})
        elif path == "/api/history":
            telegram = self.auth()
            if telegram is None:
                return
            with self.server.db() as conn:
                rows = conn.execute("""SELECT id,game,bet,cost,win,created_at FROM rounds
                    WHERE telegram_id=? ORDER BY created_at DESC,rowid DESC LIMIT 20""",
                    (telegram["id"],)).fetchall()
                self.reply(200, {"rounds": [dict(row) for row in rows]})
        elif path == "/api/wallet":
            telegram = self.auth()
            if telegram is None:
                return
            request_id = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("request_id", [""])[0]
            with self.server.db() as conn:
                user = self.server.ensure_user(conn, telegram)
                row = conn.execute("SELECT * FROM wallet_requests WHERE telegram_id=? AND request_id=?",
                                   (telegram["id"], request_id)).fetchone()
                self.reply(200, {"request": dict(row) if row else None, "manager_username": manager_username(),
                                 "user": self.server.public_user(user)})
        elif path == "/api/mines":
            telegram = self.auth()
            if telegram is None:
                return
            with self.server.db() as conn:
                user = self.server.ensure_user(conn, telegram)
                row = conn.execute("SELECT * FROM mines_rounds WHERE telegram_id=? AND status='active'",
                                   (telegram["id"],)).fetchone()
                requested = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("round_id", [""])[0]
                if row is None and requested:
                    row = conn.execute("SELECT * FROM mines_rounds WHERE id=? AND telegram_id=?",
                                       (requested, telegram["id"])).fetchone()
                self.reply(200, {"round": public_mines(row) if row else None,
                                 "user": self.server.public_user(user)})
        elif path == "/" or path.startswith("/static/"):
            filename = "index.html" if path == "/" else path.removeprefix("/static/")
            file = (STATIC_DIR / filename).resolve()
            if not file.is_relative_to(STATIC_DIR.resolve()) or not file.is_file():
                self.reply(404, {"error": "Not found"})
                return
            data = file.read_bytes()
            mime = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") or mime == "application/javascript" else mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store" if filename == "index.html" else "public, max-age=300")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
        else:
            self.reply(404, {"error": "Not found"})

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/welcome/claim":
            telegram = self.auth()
            if telegram is None:
                return
            with self.server.db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                user = self.server.ensure_user(conn, telegram)
                if user["welcome_prize"] == 0:
                    prize = secrets.SystemRandom().choice(WELCOME_REWARDS_RUB) * 100
                    conn.execute("UPDATE users SET balance=balance+?,welcome_prize=? WHERE telegram_id=?",
                                 (prize, prize, telegram["id"]))
                    user = self.server.ensure_user(conn, telegram)
                self.reply(200, {"prize": user["welcome_prize"], "user": self.server.public_user(user)})
        elif path == "/api/wallet/request":
            self.wallet_request()
        elif path == "/api/play":
            telegram = self.auth()
            if telegram is None:
                return
            data = self.body()
            bet, request_id = data.get("bet"), data.get("request_id")
            cost = bet_in_kopeks(bet)
            if cost is None:
                self.reply(400, {"error": "Недопустимая ставка"})
                return
            if data.get("bonus") not in (None, False):
                self.reply(400, {"error": "Платный бонус больше недоступен"})
                return
            try:
                request_id = str(uuid.UUID(str(request_id)))
            except (ValueError, TypeError, AttributeError):
                self.reply(400, {"error": "Неверный номер запроса"})
                return
            with self.server.db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                user = self.server.ensure_user(conn, telegram)
                prior = conn.execute("SELECT result_json,game FROM rounds WHERE telegram_id=? AND request_id=?",
                                     (telegram["id"], request_id)).fetchone()
                if prior:
                    if prior["game"] != "pickaxe":
                        self.reply(409, {"error": "Номер запроса уже использован"})
                        return
                    self.reply(200, json.loads(prior["result_json"]))
                    return
                if conn.execute("SELECT 1 FROM mines_rounds WHERE telegram_id=? AND status='active'",
                                (telegram["id"],)).fetchone():
                    self.reply(409, {"error": "Сначала завершите раунд «Мины»"})
                    return
                if user["balance"] < cost:
                    self.reply(409, {"error": "Недостаточно игровых ₽"})
                    return
                payout_limit = MAX_MONEY_KOPEKS - user["balance"] + cost
                if cost * MIN_RETURN_PERCENT // 100 > payout_limit:
                    self.reply(409, {"error": "Сумма слишком велика для баланса"})
                    return
                result = make_round(bet, payout_limit=payout_limit)
                new_balance = user["balance"] - cost + result["win"]
                result.update(id=str(uuid.uuid4()), bet=cost, cost=cost,
                              balance=new_balance, start_balance=user["balance"])
                conn.execute("UPDATE users SET balance=? WHERE telegram_id=?", (new_balance, telegram["id"]))
                conn.execute("""INSERT INTO rounds(id,telegram_id,request_id,bet,cost,win,result_json,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""", (result["id"], telegram["id"], request_id,
                    cost, cost, result["win"], json.dumps(result), int(time.time())))
                self.reply(200, result)
        elif path in ("/api/mines/start", "/api/mines/reveal", "/api/mines/cashout"):
            self.play_mines(path)
        elif path == "/telegram/webhook":
            secret = self.server.webhook_secret
            supplied = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not secret or not hmac.compare_digest(secret, supplied):
                self.reply(403, {"error": "Forbidden"})
                return
            update = self.body()
            message = update.get("message") or {}
            sender = message.get("from") or {}
            if (message.get("chat", {}).get("type") == "private" and type(sender.get("id")) is int
                    and sender["id"] > 0 and message["chat"].get("id") == sender["id"]):
                with self.server.db() as conn:
                    self.server.ensure_user(conn, sender)
            try:
                if AdminBot(self.server).handle(update):
                    self.reply(200, {"ok": True})
                    return
            except (OSError, ValueError):
                self.log_error("Telegram admin response failed")
                self.reply(502, {"error": "Telegram API unavailable"})
                return
            manager_reply = self.manager_command(message)
            if manager_reply is not None:
                try:
                    telegram_api("sendMessage", {"chat_id": message["chat"]["id"], "text": manager_reply}, self.server.bot_token)
                except (OSError, ValueError):
                    self.reply(502, {"error": "Telegram API unavailable"})
                    return
            elif message.get("chat", {}).get("type") == "private":
                text = str(message.get("text", "")).strip()
                command = text.split(maxsplit=1)[0].split("@")[0] if text else ""
                url = self.server.webapp_url.rstrip("/")
                try:
                    if command == "/start" and url.startswith("https://"):
                        send_welcome(message["chat"]["id"], url, self.server.bot_token)
                    elif command == "/support" and support_username():
                        telegram_api("sendMessage", {"chat_id": message["chat"]["id"],
                            "text": f"Поддержка: @{support_username()}",
                            "reply_markup": {"inline_keyboard": support_buttons()}}, self.server.bot_token)
                except (OSError, ValueError):
                    self.log_error("Telegram bot response failed")
                    self.reply(502, {"error": "Telegram API unavailable"})
                    return
            self.reply(200, {"ok": True})
        else:
            self.reply(404, {"error": "Not found"})

    def manager_command(self, message: dict[str, Any]) -> str | None:
        manager = manager_username()
        sender = message.get("from") or {}
        permitted = bool(manager and str(sender.get("username", "")).casefold() == manager.casefold())
        if type(sender.get("id")) is int:
            permitted = permitted or AdminBot(self.server).allowed(sender["id"])
        if message.get("chat", {}).get("type") != "private" or not permitted:
            return None
        parts = str(message.get("text", "")).split()
        command = parts[0].split("@")[0] if parts else ""
        if command == "/requests":
            with self.server.db() as conn:
                rows = conn.execute("SELECT * FROM wallet_requests WHERE status='pending' ORDER BY created_at LIMIT 10").fetchall()
            if not rows:
                return "Нет заявок на рассмотрении."
            return "Заявки на рассмотрении:\n\n" + "\n\n".join(
                f"{'Пополнение' if row['kind']=='deposit' else 'Вывод'} · {row['amount']/100:.2f} ₽\n"
                f"Пользователь: {row['telegram_id']}\n{row['id']}" for row in rows)
        if command in ("/complete", "/cancel"):
            if len(parts) != 2:
                return f"Укажите номер заявки: {command} <номер>"
            try:
                row = self.server.resolve_wallet(parts[1], command == "/complete", actor_id=sender.get("id", 0))
            except ValueError as error:
                return str(error)
            return f"Заявка {row['id']}: {'подтверждена' if row['status']=='completed' else 'отменена'}."
        return None

    def wallet_request(self):
        telegram = self.auth()
        if telegram is None:
            return
        data = self.body()
        amount, kind = amount_in_kopeks(data.get("amount")), data.get("kind")
        manager = manager_username()
        if not manager:
            self.reply(503, {"error": "Менеджер пока не указан"})
            return
        if kind == "withdraw":
            with self.server.db() as conn:
                user = self.server.ensure_user(conn, telegram)
                self.reply(200, {"request": None, "contact_only": True, "manager_username": manager,
                    "message": f"Чтобы вывести деньги, напиши менеджеру @{manager}.",
                    "user": self.server.public_user(user)})
            return
        if amount is None or kind != "deposit":
            self.reply(400, {"error": "Укажите сумму от 1 до 1 000 000 ₽, не более двух знаков после запятой"})
            return
        try:
            request_id = str(uuid.UUID(str(data.get("request_id"))))
        except (ValueError, TypeError, AttributeError):
            self.reply(400, {"error": "Неверный номер запроса"})
            return
        with self.server.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = self.server.ensure_user(conn, telegram)
            prior = conn.execute("SELECT * FROM wallet_requests WHERE telegram_id=? AND request_id=?",
                                 (telegram["id"], request_id)).fetchone()
            if prior is None:
                now = int(time.time())
                wallet_id = str(uuid.uuid4())
                conn.execute("""INSERT INTO wallet_requests(id,telegram_id,request_id,kind,amount,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?)""", (wallet_id, telegram["id"], request_id, kind, amount, now, now))
                prior = conn.execute("SELECT * FROM wallet_requests WHERE id=?", (wallet_id,)).fetchone()
                user = self.server.ensure_user(conn, telegram)
            self.reply(200, {"request": dict(prior), "manager_username": manager,
                             "user": self.server.public_user(user)})

    def play_mines(self, path: str):
        telegram = self.auth()
        if telegram is None:
            return
        data = self.body()
        with self.server.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = self.server.ensure_user(conn, telegram)
            if path == "/api/mines/start":
                bet, count = data.get("bet"), data.get("mine_count")
                cost = bet_in_kopeks(bet)
                if cost is None or type(count) is not int or count not in MINE_COUNTS:
                    self.reply(400, {"error": "Недопустимая ставка или количество мин"})
                    return
                try:
                    request_id = str(uuid.UUID(str(data.get("request_id"))))
                except (ValueError, TypeError, AttributeError):
                    self.reply(400, {"error": "Неверный номер запроса"})
                    return
                row = conn.execute("SELECT * FROM mines_rounds WHERE telegram_id=? AND request_id=?",
                                   (telegram["id"], request_id)).fetchone()
                if row is None:
                    if conn.execute("SELECT 1 FROM rounds WHERE telegram_id=? AND request_id=?",
                                    (telegram["id"], request_id)).fetchone():
                        self.reply(409, {"error": "Номер запроса уже использован"})
                        return
                    if conn.execute("SELECT 1 FROM mines_rounds WHERE telegram_id=? AND status='active'",
                                    (telegram["id"],)).fetchone():
                        self.reply(409, {"error": "У вас уже есть незавершённый раунд"})
                        return
                    if user["balance"] < cost:
                        self.reply(409, {"error": "Недостаточно игровых ₽"})
                        return
                    round_id = str(uuid.uuid4())
                    payout_limit = MAX_MONEY_KOPEKS - user["balance"] + cost
                    if cost * MIN_RETURN_PERCENT // 100 > payout_limit:
                        self.reply(409, {"error": "Сумма слишком велика для баланса"})
                        return
                    recent = conn.execute("SELECT result_json FROM rounds WHERE telegram_id=? AND game='mines' ORDER BY created_at DESC,rowid DESC LIMIT 10", (telegram["id"],)).fetchall()
                    endings = sum(json.loads(r["result_json"]).get("status") == "lost" for r in recent)
                    safe_opens = min(MINE_CELLS - count, 6 + min(4, endings) + (2 if cost * 2 >= user["balance"] else 0))
                    mines = secrets.SystemRandom().sample(range(MINE_CELLS), count)
                    conn.execute("""INSERT INTO mines_rounds(id,telegram_id,request_id,bet,mine_count,mines_json,created_at,safe_opens,payout_limit)
                        VALUES(?,?,?,?,?,?,?,?,?)""", (round_id, telegram["id"], request_id, cost, count,
                                                  json.dumps(mines), int(time.time()), safe_opens, payout_limit))
                    conn.execute("UPDATE users SET balance=balance-? WHERE telegram_id=?", (cost, telegram["id"]))
                    row = conn.execute("SELECT * FROM mines_rounds WHERE id=?", (round_id,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM mines_rounds WHERE id=? AND telegram_id=?",
                                   (str(data.get("round_id", "")), telegram["id"])).fetchone()
                if row is None:
                    self.reply(404, {"error": "Раунд не найден"})
                    return
                opened, mines = json.loads(row["opened_json"]), json.loads(row["mines_json"])
                status, win = row["status"], row["win"]
                if path == "/api/mines/reveal":
                    cell = data.get("cell")
                    if type(cell) is not int or not 0 <= cell < MINE_CELLS:
                        self.reply(400, {"error": "Неверная клетка"})
                        return
                    if status == "active" and cell not in opened:
                        if cell in mines:
                            hidden = [i for i in range(MINE_CELLS) if i not in opened and i not in mines and i != cell]
                            rng = secrets.SystemRandom()
                            if hidden and (len(opened) < row["safe_opens"] or rng.randrange(1000) < 995):
                                mines.remove(cell)
                                mines.append(rng.choice(hidden))
                        opened.append(cell)
                        if cell in mines:
                            status = "lost"
                            win = 0
                        elif len(opened) == MINE_CELLS - row["mine_count"]:
                            status = "cashed"
                            win = limited_mines_payout(row, len(opened))
                elif status == "active":
                    if not opened:
                        self.reply(409, {"error": "Сначала откройте хотя бы одну клетку"})
                        return
                    status = "cashed"
                    win = limited_mines_payout(row, len(opened))
                if row["status"] == "active":
                    conn.execute("UPDATE mines_rounds SET status=?,win=?,opened_json=?,mines_json=? WHERE id=?",
                                 (status, win, json.dumps(opened), json.dumps(mines), row["id"]))
                    row = conn.execute("SELECT * FROM mines_rounds WHERE id=?", (row["id"],)).fetchone()
                    if status != "active":
                        conn.execute("UPDATE users SET balance=balance+? WHERE telegram_id=?", (win, telegram["id"]))
                        conn.execute("""INSERT INTO rounds(id,telegram_id,request_id,bet,cost,win,result_json,created_at,game)
                            VALUES(?,?,?,?,?,?,?,?,?)""", (row["id"], telegram["id"], row["request_id"], row["bet"],
                            row["bet"], win, json.dumps(public_mines(row)), int(time.time()), "mines"))
            user = self.server.ensure_user(conn, telegram)
            self.reply(200, {"round": public_mines(row), "user": self.server.public_user(user)})


def send_welcome(chat_id: int, url: str, token: str):
    buttons = support_buttons() + [[{"text": "⚒️Играть", "style": "success", "web_app": {"url": url}}]]
    payload = {"chat_id": chat_id, "photo": url + WELCOME_BANNER_PATH,
               "caption": WELCOME_CAPTION, "parse_mode": "HTML",
               "reply_markup": {"inline_keyboard": buttons}}
    try:
        telegram_api("sendPhoto", payload, token)
    except urllib.error.HTTPError as error:
        # Retry only a definite rejection of custom emoji, never a network timeout.
        if error.code != 400 or "emoji" not in error.read().decode("utf-8", errors="replace").lower():
            raise
        payload = {**payload, "caption": re.sub(r'<tg-emoji\b[^>]*>(.*?)</tg-emoji>', r'\1', WELCOME_CAPTION)}
        telegram_api("sendPhoto", payload, token)


def telegram_api(method: str, payload: dict[str, Any], token: str) -> dict[str, Any]:
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        if method == "editMessageText" and error.code == 400:
            description = error.read().decode("utf-8", errors="replace")
            if "message is not modified" in description:
                return {"ok": True}
        raise
    if not result.get("ok"):
        raise ValueError("Telegram API rejected the request")
    return result


def register_webhook(settings: RuntimeConfig | GameServer | None = None):
    settings = settings or RuntimeConfig.from_environment()
    url, secret, token = settings.webapp_url.rstrip("/"), settings.webhook_secret, settings.bot_token
    if not url.startswith("https://") or not secret or not token:
        raise ValueError("Bothost must provide the bot token and domain; connect a Telegram bot and enable its domain")
    telegram_api("setWebhook", {"url": url + "/telegram/webhook", "secret_token": secret,
                                "allowed_updates": ["message", "callback_query"]}, token)
    telegram_api("setChatMenuButton", {"menu_button": {"type": "web_app", "text": "Играть", "web_app": {"url": url}}}, token)


def register_webhook_in_background(server: GameServer):
    if not server.bot_token or not server.webapp_url:
        print("Telegram setup: connect the bot in Bothost and enable 'Use domain'; hosting supplies the settings automatically", flush=True)
        return
    delay = 2
    while not server.stop_event.is_set():
        retry_delay = delay
        try:
            register_webhook(server)
            server.telegram_registered = True
            print("Telegram webhook and menu button registered", flush=True)
            return
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                print("Telegram rejected the bot credentials; check the bot token entered in Bothost", flush=True)
                return
            if error.code == 429:
                try:
                    retry_after = json.loads(error.read()).get("parameters", {}).get("retry_after", 0)
                    if type(retry_after) is int and retry_after > 0:
                        retry_delay = max(delay, retry_after)
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
        except (OSError, ValueError):
            pass
        print(f"Telegram setup unavailable; retrying automatically in {retry_delay}s", flush=True)
        if server.stop_event.wait(retry_delay):
            return
        delay = min(delay * 2, 60)


def start_telegram_setup(server: GameServer):
    if not server.register_webhook:
        return None
    worker = threading.Thread(target=register_webhook_in_background, args=(server,), daemon=True)
    worker.start()
    return worker


if __name__ == "__main__":
    settings = RuntimeConfig.from_environment()
    if len(sys.argv) > 1 and sys.argv[1] in ("wallet-list", "wallet-complete", "wallet-cancel"):
        storage = object.__new__(GameServer)
        storage.db_path = settings.db_path
        storage._init_db()
        if sys.argv[1] == "wallet-list":
            with storage.db() as conn:
                rows = conn.execute("SELECT * FROM wallet_requests WHERE status='pending' ORDER BY created_at LIMIT 50").fetchall()
            print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2))
        else:
            if len(sys.argv) != 3:
                sys.exit("Укажите номер заявки")
            try:
                print(json.dumps(storage.resolve_wallet(sys.argv[2], sys.argv[1] == "wallet-complete"), ensure_ascii=False))
            except ValueError as error:
                sys.exit(str(error))
    elif len(sys.argv) > 1 and sys.argv[1] == "set-webhook":
        try:
            register_webhook(settings)
        except (OSError, ValueError):
            sys.exit("Telegram setup failed; check the bot token and domain in Bothost")
        print("Webhook registered")
    else:
        server = GameServer(("0.0.0.0", settings.port), config=settings)
        print(f"Pickaxe Drop listening on port {server.server_port}", flush=True)
        start_telegram_setup(server)
        server.start_broadcast_worker()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
