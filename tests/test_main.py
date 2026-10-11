import hashlib
import hmac
import io
import json
import tempfile
import time
import unittest
import urllib.parse
import uuid
import math
import wave
from pathlib import Path
from unittest.mock import patch

import main
from main import GameHandler, GameServer, verify_telegram_init_data

TOKEN = "123456:test-token"


def signed_data(user_id=42, now=None):
    fields = {"auth_date": str(now or int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "Тест", "username": "test_user"})}
    string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


class MemoryConnection:
    """Exercise the real HTTP handler without opening a sandbox network socket."""
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, mode, buffering=None):
        return self.input if "r" in mode else self.output

    def sendall(self, data):
        self.output.write(data)


class PickaxeAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = object.__new__(GameServer)
        self.server.db_path = str(Path(self.temp.name) / "game.sqlite3")
        self.server.bot_token = TOKEN
        self.server.dev_mode = False
        self.server.webapp_url = ""
        self.server.webhook_secret = "test_secret"
        self.server._init_db()
        self.auth = signed_data()
        with patch("main.secrets.SystemRandom") as rng:
            rng.return_value.choice.return_value = 500
            self.call("/api/welcome/claim", {})

    def tearDown(self):
        self.temp.cleanup()

    def call(self, path, data=None, auth=True, extra_headers=""):
        body = json.dumps(data).encode() if data is not None else b""
        headers = f"Host: localhost\r\nContent-Length: {len(body)}\r\n"
        if auth:
            headers += f"X-Telegram-Init-Data: {self.auth}\r\n"
        headers += extra_headers
        raw = (f"{'POST' if data is not None else 'GET'} {path} HTTP/1.1\r\n"
               f"{headers}\r\n").encode() + body
        connection = MemoryConnection(raw)
        GameHandler(connection, ("127.0.0.1", 54321), self.server)
        head, content = connection.output.getvalue().split(b"\r\n\r\n", 1)
        status = int(head.split(b" ", 2)[1])
        if path == "/":
            return status, content
        return status, json.loads(content)

    def test_signature_and_stale_data(self):
        self.assertEqual(verify_telegram_init_data(self.auth, TOKEN)["id"], 42)
        self.assertIsNone(verify_telegram_init_data(self.auth.replace("test_user", "other"), TOKEN))
        self.assertIsNone(verify_telegram_init_data(signed_data(now=int(time.time()) - 90_000), TOKEN))
        self.assertEqual(self.call("/api/me", auth=False)[0], 401)

    def test_first_login_and_atomic_round(self):
        first = self.call("/api/me")[1]["user"]
        self.assertEqual(first["balance"], 50_000)
        self.assertEqual(first["name"], "Тест")
        self.assertEqual(self.call("/api/me")[1]["user"]["balance"], 50_000)
        request = {"bet": 10, "bonus": False, "request_id": str(uuid.uuid4())}
        status, round_one = self.call("/api/play", request)
        self.assertEqual(status, 200)
        self.assertGreater(len(round_one["steps"]), 0)
        self.assertIsInstance(round_one["physics_seed"], int)
        self.assertGreaterEqual(round_one["physics_seed"], 0)
        self.assertLess(round_one["physics_seed"], 2**32)
        self.assertEqual(round_one["balance"], 50_000 - round_one["cost"] + round_one["win"])
        self.assertEqual(round_one["win"], sum(step["gain"] for step in round_one["steps"]))
        self.assertEqual(round_one["cost"], 1000)
        self.assertIn(round_one["pickaxe"], main.PICKAXES)
        self.assertEqual(round_one["max_hp"], round_one["pickaxe"]["max_hp"])
        self.assertTrue(all(0 <= step["hp"] <= round_one["max_hp"] for step in round_one["steps"]))
        self.assertEqual(self.call("/api/play", request)[1], round_one)
        self.assertEqual(self.call("/api/me")[1]["user"]["balance"], round_one["balance"])
        self.assertEqual(self.call("/api/me")[1]["user"]["rounds_count"], 1)
        self.assertEqual(len(self.call("/api/history")[1]["rounds"]), 1)
        self.assertEqual(self.call("/api/play", {"bet": 10, "bonus": True,
                          "request_id": str(uuid.uuid4())})[0], 400)
        self.assertEqual(self.call("/api/me")[1]["user"]["balance"], round_one["balance"])
        with self.server.db() as conn:
            conn.execute("UPDATE users SET balance=1 WHERE telegram_id=42")
        self.assertEqual(self.call("/api/play", {"bet": 10,
                          "request_id": str(uuid.uuid4())})[0], 409)
        self.assertEqual(self.call("/api/play", {"bet": True, "request_id": str(uuid.uuid4())})[0], 400)

    def test_static_and_webhook_guard(self):
        html = self.call("/")[1]
        self.assertIn(b"Pickaxe Drop", html)
        self.assertIn(b"<style>", html)
        self.assertIn(b"const tg = window.Telegram", html)
        self.assertNotIn(b'href="/static/styles.css"', html)
        self.assertNotIn(b'src="/static/app.js"', html)
        self.assertNotIn(b'src="/static/physics.js"', html)
        self.assertNotIn(b'src="/static/sound.js"', html)
        self.assertIn(b"data:audio/wav;base64,", html)
        self.assertNotIn(b'id="bonus-button"', html)
        self.assertEqual(self.call("/telegram/webhook", {"message": {"text": "/start"}})[0], 403)
        self.assertEqual(self.call("/static/../../main.py")[0], 404)

    def test_server_decides_pickaxe_and_publishes_chances(self):
        self.assertEqual(self.call("/api/me")[1]["pickaxes"], list(main.PICKAXES))
        self.assertEqual(sum(p["chance"] for p in main.PICKAXES), 100)
        with patch("main.secrets.SystemRandom", return_value=FixedRoundRandom(main.PICKAXES[0], "stone")):
            status, result = self.call("/api/play", {"bet": 10, "pickaxe": "diamond", "max_hp": 250,
                                      "request_id": str(uuid.uuid4())})
        self.assertEqual(status, 200)
        self.assertEqual(result["pickaxe"]["id"], "stone")
        self.assertEqual(result["max_hp"], 100)
        self.assertEqual(result["steps"][0]["hp"], 97)
        self.assertEqual(result["cost"], 1000)

    def start_mines(self, count=3, bet=10, request_id=None):
        request = {"bet": bet, "mine_count": count, "request_id": request_id or str(uuid.uuid4())}
        with patch("main.secrets.SystemRandom") as rng:
            rng.return_value.sample.return_value = list(range(count))
            return request, self.call("/api/mines/start", request)

    def test_mines_secret_board_debit_recovery_and_loss(self):
        request, (status, data) = self.start_mines()
        self.assertEqual(status, 200)
        game = data["round"]
        self.assertEqual(data["user"]["balance"], 49_000)
        self.assertNotIn("mines", game)
        self.assertEqual(self.call("/api/mines/start", request)[1], data)
        self.assertEqual(self.call("/api/mines")[1]["round"], game)
        self.assertEqual(self.call("/api/mines/cashout", {"round_id": game["id"]})[0], 409)
        self.assertEqual(self.start_mines()[1][0], 409)
        self.assertEqual(self.call("/api/play", {"bet": 10, "request_id": str(uuid.uuid4())})[0], 409)
        for cell in (True, -1, 25, "3"):
            self.assertEqual(self.call("/api/mines/reveal", {"round_id": game["id"], "cell": cell})[0], 400)
        for cell in (3, 4):
            body = {"round_id": game["id"], "cell": cell}
            status, safe = self.call("/api/mines/reveal", body)
            self.assertEqual(status, 200)
            self.assertNotIn("mines", safe["round"])
            self.assertEqual(safe["round"]["payout"], main.mines_payout(1000, 3, cell - 2))
            self.assertEqual(self.call("/api/mines/reveal", body)[1], safe)
        with self.server.db() as conn:
            conn.execute("UPDATE mines_rounds SET safe_opens=0 WHERE id=?", (game["id"],))
        with patch("main.secrets.SystemRandom") as rng:
            rng.return_value.randrange.return_value = 999
            lost = self.call("/api/mines/reveal", {"round_id": game["id"], "cell": 0})[1]
        self.assertEqual(lost["round"]["status"], "lost")
        self.assertEqual(lost["round"]["win"], main.mines_payout(1000, 3, 2) + 50)
        self.assertEqual(lost["round"]["mines"], [0, 1, 2])
        self.assertEqual(lost["round"]["safe_count"], 2)
        self.assertEqual(lost["user"]["balance"], 49_000 + lost["round"]["win"])
        self.assertEqual(self.call("/api/mines?round_id="+game["id"])[1], lost)
        self.assertEqual(self.call("/api/mines/cashout", {"round_id": game["id"]})[1], lost)
        self.assertEqual(self.call("/api/me")[1]["user"]["rounds_count"], 1)
        self.assertEqual(self.call("/api/history")[1]["rounds"][0]["game"], "mines")

    def test_mines_cashout_is_atomic_and_scoped_to_user(self):
        _, (_, data) = self.start_mines(5)
        game = data["round"]
        safe = self.call("/api/mines/reveal", {"round_id": game["id"], "cell": 5})[1]
        self.assertEqual(safe["round"]["payout"], 1200)
        body = {"round_id": game["id"], "win": 999999999}
        result = self.call("/api/mines/cashout", body)[1]
        self.assertEqual(result["round"]["status"], "cashed")
        self.assertEqual(result["round"]["win"], 1200)
        self.assertEqual(result["user"]["balance"], 50_200)
        self.assertEqual(self.call("/api/mines/cashout", body)[1], result)
        self.assertEqual(self.call("/api/mines/reveal", {"round_id": game["id"], "cell": 0})[1], result)
        self.assertEqual(self.call("/api/history")[1]["rounds"][0]["cost"], 1000)
        self.auth = signed_data(43)
        self.assertEqual(self.call("/api/mines/cashout", body)[0], 404)
        self.assertIsNone(self.call("/api/mines?round_id="+game["id"])[1]["round"])
        self.auth = signed_data()
        self.assertEqual(self.call("/api/me")[1]["user"]["balance"], 50_200)

    def test_mines_all_safe_cells_settle_once_and_validate_start(self):
        for bet, count in ((True, 3), (10, True), (0, 3), (10, 1)):
            self.assertEqual(self.call("/api/mines/start", {"bet": bet, "mine_count": count,
                             "request_id": str(uuid.uuid4())})[0], 400)
        self.assertEqual(self.call("/api/mines/start", {"bet": 10, "mine_count": 3, "request_id": "bad"})[0], 400)
        request, (_, data) = self.start_mines(7)
        game = data["round"]
        for cell in range(7, 25):
            result = self.call("/api/mines/reveal", {"round_id": game["id"], "cell": cell})[1]
        self.assertEqual(result["round"]["status"], "cashed")
        self.assertEqual(result["round"]["win"], main.mines_payout(1000, 7, 18))
        self.assertEqual(result["user"]["balance"], 49_000 + result["round"]["win"])
        self.assertEqual(self.call("/api/mines/start", request)[1], result)
        self.assertEqual(len(self.call("/api/history")[1]["rounds"]), 1)
        with self.server.db() as conn:
            conn.execute("UPDATE users SET balance=1 WHERE telegram_id=42")
        self.assertEqual(self.start_mines()[1][0], 409)

    def test_mines_migrates_existing_database_without_resetting_balance(self):
        self.call("/api/me")
        with self.server.db() as conn:
            conn.execute("UPDATE users SET balance=43210 WHERE telegram_id=42")
            conn.execute("ALTER TABLE rounds DROP COLUMN game")
        self.server._init_db()
        self.assertEqual(self.call("/api/me")[1]["user"]["balance"], 43210)
        self.assertEqual(self.start_mines()[1][0], 200)

    def test_welcome_prizes_are_server_chosen_and_credited_once(self):
        for i, prize in enumerate(main.WELCOME_REWARDS_RUB):
            self.auth = signed_data(100+i)
            user = self.call("/api/me")[1]["user"]
            self.assertEqual(user["balance"], 0)
            self.assertTrue(user["welcome_available"])
            with patch("main.secrets.SystemRandom") as rng:
                rng.return_value.choice.return_value = prize
                status, result = self.call("/api/welcome/claim", {"prize": 99999999})
            self.assertEqual(status, 200)
            self.assertEqual(result["prize"], prize*100)
            self.assertEqual(result["user"]["balance"], prize*100)
            self.assertFalse(result["user"]["welcome_available"])
            self.assertEqual(self.call("/api/welcome/claim", {})[1], result)
            self.server._init_db()
            self.assertEqual(self.call("/api/me")[1]["user"]["balance"], prize*100)
        self.assertEqual(self.call("/api/welcome/claim", {}, auth=False)[0], 401)

    def test_wallet_manager_amount_validation_and_manual_deposit(self):
        request={"kind":"deposit","amount":"123,45","request_id":str(uuid.uuid4())}
        with patch("main.MANAGER_USERNAME", ""):
            self.assertEqual(self.call("/api/wallet/request", request)[0], 503)
        with patch("main.MANAGER_USERNAME", "@Valid_Manager"):
            self.assertEqual(self.call("/api/me")[1]["manager_username"], "Valid_Manager")
            for amount in (True, 100, "nan", "1e3", "0", "-10", "1.001", "1000001", "<script>"):
                self.assertEqual(self.call("/api/wallet/request", {**request,"amount":amount})[0], 400)
            status,result=self.call("/api/wallet/request",request)
            self.assertEqual(status,200)
            self.assertEqual(result["user"]["balance"],50000)
            self.assertEqual(result["request"]["amount"],12345)
            self.assertEqual(self.call("/api/wallet/request",request)[1],result)
            self.assertEqual(self.call("/api/wallet?request_id="+request["request_id"])[1],result)
        request_id=result["request"]["id"]
        self.server.resolve_wallet(request_id,True)
        self.server.resolve_wallet(request_id,True)
        self.server.resolve_wallet(request_id,False)
        self.assertEqual(self.call("/api/me")[1]["user"]["balance"],62345)
        self.auth=signed_data(43)
        self.assertIsNone(self.call("/api/wallet?request_id="+request["request_id"])[1]["request"])

    def test_wallet_withdrawal_hold_and_cancellation_are_atomic(self):
        request={"kind":"withdraw","amount":"200","request_id":str(uuid.uuid4())}
        with patch("main.MANAGER_USERNAME", "Valid_Manager"):
            self.assertEqual(self.call("/api/wallet/request",{**request,"amount":"501"})[0],409)
            status,result=self.call("/api/wallet/request",request)
            self.assertEqual(status,200)
            self.assertEqual(result["user"]["balance"],30000)
            self.assertEqual(self.call("/api/wallet/request",request)[1],result)
            self.server.resolve_wallet(result["request"]["id"],False)
            self.server.resolve_wallet(result["request"]["id"],False)
            self.assertEqual(self.call("/api/me")[1]["user"]["balance"],50000)
            request["request_id"]=str(uuid.uuid4())
            completed=self.call("/api/wallet/request",request)[1]
            self.server.resolve_wallet(completed["request"]["id"],True)
            self.server.resolve_wallet(completed["request"]["id"],True)
            self.assertEqual(self.call("/api/me")[1]["user"]["balance"],30000)
            self.start_mines()
            request["request_id"]=str(uuid.uuid4())
            self.assertEqual(self.call("/api/wallet/request",request)[0],409)

    def test_only_configured_manager_can_confirm_in_private_bot_chat(self):
        request={"kind":"deposit","amount":"100","request_id":str(uuid.uuid4())}
        with patch("main.MANAGER_USERNAME", "Valid_Manager"):
            row=self.call("/api/wallet/request",request)[1]["request"]
            payload={"message":{"from":{"username":"intruder"},"chat":{"id":123,"type":"private"},
                                "text":"/complete "+row["id"]}}
            header="X-Telegram-Bot-Api-Secret-Token: test_secret\r\n"
            with patch("main.telegram_api") as api:
                self.call("/telegram/webhook",payload,extra_headers=header)
                api.assert_not_called()
                self.assertEqual(self.call("/api/me")[1]["user"]["balance"],50000)
                payload["message"]["from"]["username"]="valid_manager"
                self.call("/telegram/webhook",payload,extra_headers=header)
                self.assertEqual(api.call_count,1)
                self.call("/telegram/webhook",payload,extra_headers=header)
                self.assertEqual(self.call("/api/me")[1]["user"]["balance"],60000)
                payload["message"]["chat"]["type"]="group"
                payload["message"]["text"]="/requests"
                self.call("/telegram/webhook",payload,extra_headers=header)
                self.assertEqual(api.call_count,2)


class FixedRoundRandom:
    def __init__(self, pickaxe, kind):
        self.pickaxe, self.kind = pickaxe, kind

    def choices(self, population, *, weights, k):
        if isinstance(population[0], dict):
            assert list(weights) == [50, 35, 15]
            return [self.pickaxe]
        return [self.kind]

    def choice(self, values):
        return 0

    def randint(self, a, b):
        return (a + b) // 2

    def getrandbits(self, bits):
        return 27


class PickaxeDurabilityTests(unittest.TestCase):
    def test_every_pickaxe_tier_and_stake_returns_a_profit_with_visible_mine_bonus(self):
        for pickaxe in main.PICKAXES:
            for bet in (1, 10, 20, 50, 100, 250, 500, 501, 10000):
                for kind in ("stone", "tnt", "heart", "gold", "boost"):
                    with patch("main.secrets.SystemRandom",return_value=FixedRoundRandom(pickaxe,kind)):
                        result=main.make_round(bet)
                    self.assertGreaterEqual(result["win"],bet*110)
                    self.assertEqual(result["win"],sum(step["gain"] for step in result["steps"]))
                    self.assertGreater(result["mine_bonus"],0)
                    self.assertTrue(any(step["kind"]=="tnt" and step["gain"]>0 for step in result["steps"]))

    def test_all_tiers_have_their_own_health_cap_and_survival_depth(self):
        for pickaxe in main.PICKAXES:
            with self.subTest(pickaxe=pickaxe["id"]):
                with patch("main.secrets.SystemRandom", return_value=FixedRoundRandom(pickaxe, "heart")):
                    healed = main.make_round(10)
                self.assertEqual(len(healed["steps"]), main.MAX_DEPTH)
                self.assertTrue(all(step["hp"] == pickaxe["max_hp"] for step in healed["steps"][:-1]))
                self.assertEqual(healed["steps"][-1]["hp"],pickaxe["max_hp"]-20)
                with patch("main.secrets.SystemRandom", return_value=FixedRoundRandom(pickaxe, "tnt")):
                    destroyed = main.make_round(10)
                self.assertEqual(destroyed["depth"], math.ceil(pickaxe["max_hp"] / 20))
                self.assertEqual(destroyed["health"], 0)
                self.assertTrue(all(step["hp"] > 0 for step in destroyed["steps"][:-1]))

    def test_audio_assets_are_short_valid_pcm_clips(self):
        for name in ("start", "hit", "ore", "blast", "pickup", "finish"):
            with wave.open(str(main.STATIC_DIR / f"assets/sounds/{name}.wav"), "rb") as clip:
                self.assertEqual(clip.getnchannels(), 1)
                self.assertEqual(clip.getsampwidth(), 2)
                self.assertEqual(clip.getframerate(), 22050)
                self.assertTrue(.1 < clip.getnframes() / clip.getframerate() < 1)
                self.assertNotEqual(set(clip.readframes(clip.getnframes())), {0})


if __name__ == "__main__":
    unittest.main()
