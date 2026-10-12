import io
import json
import time
import unittest
import urllib.error
import uuid
from unittest.mock import patch

import main
import test_main as support
import test_admin as admin_support


class BroadcastTests(unittest.TestCase):
    call = support.PickaxeAppTests.call
    message = admin_support.AdminAndWagerTests.message
    callback = admin_support.AdminAndWagerTests.callback

    def setUp(self):
        support.PickaxeAppTests.setUp(self)
        self.worker_patch = patch.object(self.server, "start_broadcast_worker")
        self.worker_start = self.worker_patch.start()

    def tearDown(self):
        self.worker_patch.stop()
        support.PickaxeAppTests.tearDown(self)

    def draft(self, text="Всем привет!", entities=None):
        self.message("/ClezzyKryt")
        self.callback("adm:broadcast_new")
        with patch("main.telegram_api", return_value={"ok": True}) as api:
            response = self.call("/telegram/webhook", {"message": {"text": text, "entities": entities or [],
                "from": {"id": 99, "first_name": "Администратор"}, "chat": {"id": 99, "type": "private"}}},
                auth=False, extra_headers="X-Telegram-Bot-Api-Secret-Token: test_secret\r\n")
        self.assertEqual(response[0], 200)
        self.assertTrue(all(call.args[1]["chat_id"] == 99 for call in api.call_args_list))
        action = api.call_args_list[-1].args[1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        return action, api.call_args_list

    def test_preview_confirmation_keeps_formatting_and_queues_each_recipient_once(self):
        self.message("/start", user=100)  # Has never opened Mini App.
        text = "  🎉 Новая игра!  "
        entities = [{"type": "bold", "offset": 5, "length": 5}]
        action, calls = self.draft(text, entities)
        self.assertEqual(calls[0].args[1]["text"], text)
        self.assertEqual(calls[0].args[1]["entities"], entities)
        with self.server.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM broadcasts").fetchone()[0], 0)
        self.callback(action)
        self.callback(action)
        with self.server.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM broadcasts").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM admin_audit WHERE action='broadcast_start'").fetchone()[0], 1)
            recipients = [r[0] for r in conn.execute("SELECT telegram_id FROM broadcast_deliveries ORDER BY telegram_id")]
            self.assertEqual(recipients, [42, 99, 100])
        self.worker_start.assert_called()
        worker = main.BroadcastWorker(self.server)
        with patch("main.telegram_api", return_value={"ok": True}) as api:
            for _ in recipients:
                self.assertEqual(worker.process_one(), 0.05)
            self.assertIsNone(worker.process_one())
        self.assertEqual([call.args[1]["chat_id"] for call in api.call_args_list], recipients)
        for call in api.call_args_list:
            self.assertEqual(call.args[0], "sendMessage")
            self.assertEqual(call.args[1]["text"], text)
            self.assertEqual(call.args[1]["entities"], entities)
            self.assertFalse(call.args[1]["disable_notification"])
        report = main.AdminBot(self.server).broadcast_status(action.rsplit(":", 1)[1])[0]
        self.assertIn("Доставлено: 3", report)
        self.assertIn("Завершена", report)
        self.callback("adm:audit")

    def test_cancelled_expired_foreign_and_forwarded_confirmations_never_send(self):
        action, _ = self.draft()
        self.callback(action, user=100)
        self.callback(action, user=99, chat_id=100)
        self.callback("adm:broadcast")
        self.callback(action)
        with self.server.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM broadcasts").fetchone()[0], 0)
        self.worker_start.assert_not_called()
        action, _ = self.draft()
        with self.server.db() as conn:
            conn.execute("UPDATE admin_sessions SET expires_at=0")
        self.callback(action)
        self.worker_start.assert_not_called()

    def test_rate_limit_retries_and_blocked_or_uncertain_deliveries_do_not_repeat(self):
        action, _ = self.draft()
        self.callback(action)
        limited = urllib.error.HTTPError("https://api.telegram.org", 429, "limited", {},
            io.BytesIO(b'{"parameters":{"retry_after":90}}'))
        blocked = urllib.error.HTTPError("https://api.telegram.org", 403, "blocked", {}, None)
        worker = main.BroadcastWorker(self.server)
        with patch("main.telegram_api", side_effect=[limited, blocked, TimeoutError("response lost")]) as api:
            self.assertEqual(worker.process_one(), 90)
            worker.process_one()
            worker.process_one()
            self.assertIsNone(worker.process_one())
        self.assertEqual([call.args[1]["chat_id"] for call in api.call_args_list], [42, 42, 99])
        self.server._init_db()
        with patch("main.telegram_api") as api:
            self.assertIsNone(worker.process_one())
            api.assert_not_called()
        report = main.AdminBot(self.server).broadcast_status(action.rsplit(":", 1)[1])[0]
        self.assertIn("Не доставлено: 1", report)
        self.assertIn("Доставка не подтверждена: 1", report)

    def test_stopping_a_job_keeps_sent_messages_and_cancels_remaining(self):
        action, _ = self.draft()
        self.callback(action)
        worker = main.BroadcastWorker(self.server)
        with patch("main.telegram_api", return_value={"ok": True}) as api:
            worker.process_one()
            self.callback("adm:broadcast_cancel:" + action.rsplit(":", 1)[1])
            self.assertIsNone(worker.process_one())
            api.assert_called_once()
        with self.server.db() as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT status FROM broadcast_deliveries ORDER BY telegram_id")], ["sent", "cancelled"])
        self.callback("adm:audit")

    def test_restart_resumes_pending_and_does_not_resend_an_interrupted_delivery(self):
        action, _ = self.draft()
        self.callback(action)
        with self.server.db() as conn:
            conn.execute("UPDATE broadcast_deliveries SET status='processing',claimed_at=? WHERE telegram_id=42", (int(time.time()) - 120,))
        self.server._init_db()
        with patch("main.telegram_api", return_value={"ok": True}) as api:
            worker = main.BroadcastWorker(self.server)
            worker.process_one()
            self.assertIsNone(worker.process_one())
        api.assert_called_once()
        self.assertEqual(api.call_args.args[1]["chat_id"], 99)

    def test_old_withdrawal_reservation_is_released_once_on_upgrade(self):
        now = int(time.time())
        request_id = str(uuid.uuid4())
        with self.server.db() as conn:
            conn.execute("UPDATE users SET balance=30000 WHERE telegram_id=42")
            conn.execute("INSERT INTO wallet_requests VALUES(?,?,?,?,?,?,?,?)", (request_id, 42, str(uuid.uuid4()), "withdraw", 20000, "pending", now, now))
        self.server._init_db()
        self.server._init_db()
        self.assertEqual(self.call("/api/me")[1]["user"]["balance"], 50000)
        self.server.resolve_wallet(request_id, False)
        self.assertEqual(self.call("/api/me")[1]["user"]["balance"], 50000)
        with self.server.db() as conn:
            self.assertEqual(conn.execute("SELECT status FROM wallet_requests WHERE id=?", (request_id,)).fetchone()[0], "cancelled")

    def test_pickaxe_total_never_exceeds_twice_stake_and_matches_the_replay(self):
        for pickaxe in main.PICKAXES:
            for kind in ("diamond", "boost", "stone", "tnt"):
                for bet in ("1.01", "10", "5000.25"):
                    with self.subTest(pickaxe=pickaxe["id"], kind=kind, bet=bet), patch("main.secrets.SystemRandom", return_value=support.FixedRoundRandom(pickaxe, kind)):
                        result = main.make_round(bet)
                    cost = main.bet_in_kopeks(bet)
                    self.assertLessEqual(result["win"], cost * 2)
                    self.assertGreaterEqual(result["win"], cost * main.MIN_RETURN_PERCENT // 100)
                    self.assertEqual(result["win"], sum(s["gain"] for s in result["steps"]))
                    self.assertTrue(all(s["gain"] >= 0 and s["multiplier"] <= 2 for s in result["steps"]))
                    self.assertGreater(result["mine_bonus"], 0)


if __name__ == "__main__":
    unittest.main()
