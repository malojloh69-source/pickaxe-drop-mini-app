import json
import unittest
import uuid
from unittest.mock import patch

import main
import test_main as support


class AdminAndWagerTests(unittest.TestCase):
    setUp = support.PickaxeAppTests.setUp
    tearDown = support.PickaxeAppTests.tearDown
    call = support.PickaxeAppTests.call
    start_mines = support.PickaxeAppTests.start_mines

    def message(self, text, user=99, chat_type="private"):
        with patch("main.telegram_api", return_value={"ok": True}) as api:
            response = self.call("/telegram/webhook", {"message": {
                "text": text, "from": {"id": user, "first_name": "Администратор"},
                "chat": {"id": user, "type": chat_type}}}, auth=False,
                extra_headers="X-Telegram-Bot-Api-Secret-Token: test_secret\r\n")
            self.assertEqual(response[0], 200)
            return api.call_args_list

    def callback(self, data, user=99, chat_id=None):
        with patch("main.telegram_api", return_value={"ok": True}) as api:
            response = self.call("/telegram/webhook", {"callback_query": {
                "id": "query", "data": data, "from": {"id": user}, "message": {
                    "message_id": 1, "chat": {"id": user if chat_id is None else chat_id, "type": "private"}}}},
                auth=False, extra_headers="X-Telegram-Bot-Api-Secret-Token: test_secret\r\n")
            self.assertEqual(response[0], 200)
            return api.call_args_list

    def test_access_code_is_private_and_persists_by_telegram_id(self):
        self.message("/ClezzyKryt", chat_type="group")
        self.assertFalse(main.AdminBot(self.server).allowed(99))
        self.message("/clezzykryt")
        self.assertFalse(main.AdminBot(self.server).allowed(99))
        calls = self.message("/ClezzyKryt")
        self.assertTrue(main.AdminBot(self.server).allowed(99))
        self.server._init_db()
        self.assertIn("Панель администратора", self.message("/admin")[-1].args[1]["text"])
        for row in calls[-1].args[1]["reply_markup"]["inline_keyboard"]:
            for button in row:
                self.assertLessEqual(len(button["callback_data"].encode()), 64)
        self.assertEqual(self.message("привет"), [])
        self.callback("adm:logout")
        self.assertFalse(main.AdminBot(self.server).allowed(99))

    def test_foreign_and_forwarded_admin_callbacks_are_rejected(self):
        self.message("/ClezzyKryt")
        for user, chat in ((100, 100), (99, 100)):
            calls = self.callback("adm:set:42", user, chat)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].args[0], "answerCallbackQuery")
            self.assertTrue(calls[0].args[1]["show_alert"])
        self.assertEqual(self.call("/api/me")[1]["user"]["balance"], 50000)

    def test_balance_edit_requires_confirmation_and_cannot_be_replayed(self):
        self.message("/ClezzyKryt")
        self.callback("adm:set:42")
        calls = self.message("1234,56")
        action = calls[-1].args[1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        self.assertEqual(self.call("/api/me")[1]["user"]["balance"], 50000)
        self.callback(action)
        self.assertEqual(self.call("/api/me")[1]["user"]["balance"], 123456)
        with self.server.db() as conn:
            conn.execute("UPDATE users SET balance=120000 WHERE telegram_id=42")
        self.callback(action)
        self.assertEqual(self.call("/api/me")[1]["user"]["balance"], 120000)
        self.callback("adm:set:42")
        self.callback("adm:user:42")
        self.assertEqual(self.message("999"), [])
        self.callback("adm:set:42")
        calls = self.message("0")
        self.callback(calls[-1].args[1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"])
        self.assertEqual(self.call("/api/me")[1]["user"]["balance"], 0)

    def test_admin_confirms_deposits_and_refunds_withdrawals_once(self):
        self.message("/ClezzyKryt")
        with patch("main.MANAGER_USERNAME", "Valid_Manager"):
            request = self.call("/api/wallet/request", {"kind": "deposit", "amount": "750,25", "request_id": str(uuid.uuid4())})[1]["request"]
            for _ in range(2):
                self.callback(f"adm:resolve:{request['id']}:1")
            self.assertEqual(self.call("/api/me")[1]["user"]["balance"], 125025)
            withdrawal = self.call("/api/wallet/request", {"kind": "withdraw", "amount": "500", "request_id": str(uuid.uuid4())})[1]["request"]
            self.assertEqual(self.call("/api/me")[1]["user"]["balance"], 75025)
            for _ in range(2):
                self.callback(f"adm:resolve:{withdrawal['id']}:0")
            self.assertEqual(self.call("/api/me")[1]["user"]["balance"], 125025)
        with self.server.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM admin_audit").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT DISTINCT actor_id FROM admin_audit").fetchone()[0], 99)
        for action in ("requests:pending:0", "requests:completed:0", "requests:cancelled:0", "users:0", "user:42", "history:42", "stats", "audit", "search"):
            self.assertEqual(self.callback("adm:" + action)[-1].args[0], "editMessageText")
        self.assertIn("Результаты поиска", self.message("42")[-1].args[1]["text"])

    def test_any_affordable_wager_above_500_and_decimal_kopeks(self):
        with self.server.db() as conn:
            conn.execute("UPDATE users SET balance=2000000 WHERE telegram_id=42")
        for bet in (501, "1234,56", "1000.01"):
            status, result = self.call("/api/play", {"bet": bet, "request_id": str(uuid.uuid4())})
            self.assertEqual(status, 200)
            self.assertEqual(result["cost"], main.bet_in_kopeks(bet))
            self.assertEqual(result["win"], sum(step["gain"] for step in result["steps"]))
        for bet in (True, 0, -1, 500.5, "0.99", "1.001", "1e5", "NaN", "999999999999999"):
            self.assertEqual(self.call("/api/play", {"bet": bet, "request_id": str(uuid.uuid4())})[0], 400)
        with self.server.db() as conn:
            conn.execute("UPDATE users SET balance=60000 WHERE telegram_id=42")
        self.assertEqual(self.call("/api/play", {"bet": 601, "request_id": str(uuid.uuid4())})[0], 409)
        _, (status, result) = self.start_mines(3, bet="600")
        self.assertEqual(status, 200)
        self.assertEqual(result["round"]["bet"], 60000)

    def test_mines_protects_first_opens_without_exposing_internal_parameters(self):
        _, (_, data) = self.start_mines(7)
        game = data["round"]
        with patch("main.secrets.SystemRandom") as rng:
            rng.return_value.choice.side_effect = lambda cells: cells[0]
            rng.return_value.randrange.return_value = 999
            for cell in range(6):
                status, result = self.call("/api/mines/reveal", {"round_id": game["id"], "cell": cell})
                self.assertEqual(status, 200)
                self.assertEqual(result["round"]["status"], "active")
                self.assertEqual(result["round"]["safe_count"], cell + 1)
                self.assertNotIn("mines", result["round"])
                self.assertNotIn("safe_opens", result["round"])
        with self.server.db() as conn:
            row = conn.execute("SELECT * FROM mines_rounds WHERE id=?", (game["id"],)).fetchone()
            self.assertEqual(len(set(json.loads(row["mines_json"]))), 7)
            self.assertFalse(set(json.loads(row["opened_json"])) & set(json.loads(row["mines_json"])))
        result = self.call("/api/mines/cashout", {"round_id": game["id"]})[1]
        self.assertGreater(result["user"]["balance"], 50000)
        self.assertEqual(self.call("/api/mines/cashout", {"round_id": game["id"]})[1], result)


if __name__ == "__main__":
    unittest.main()
