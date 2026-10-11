import contextlib
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
import test_main as support


class BootstrapTests(unittest.TestCase):
    def test_only_bothost_supplied_values_are_required(self):
        for token_key in ("BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "API_TOKEN"):
            for domain in ("bot-1791715412-5398-ggsell.bothost.tech",
                           "https://bot-1791715412-5398-ggsell.bothost.tech/",
                           "http://bot-1791715412-5398-ggsell.bothost.tech/"):
                with self.subTest(token_key=token_key, domain=domain):
                    config = main.RuntimeConfig.from_environment({token_key: support.TOKEN, "DOMAIN": domain})
                    self.assertEqual(config.bot_token, support.TOKEN)
                    self.assertEqual(config.webapp_url, "https://bot-1791715412-5398-ggsell.bothost.tech")
                    self.assertEqual(config.port, 3000)
                    self.assertTrue(config.register_webhook)
                    self.assertEqual(Path(config.db_path), main.BASE_DIR / "data/pickaxe.sqlite3")
                    self.assertRegex(config.webhook_secret, r"^[0-9a-f]{64}$")
                    self.assertNotIn(support.TOKEN, repr(config))
                    self.assertNotIn(config.webhook_secret, repr(config))

    def test_platform_id_fallback_and_secret_are_stable_across_restarts(self):
        first = main.RuntimeConfig.from_environment({"BOT_TOKEN": support.TOKEN,
            "BOT_ID": "bot_1791715412_5398_ggsell"})
        second = main.RuntimeConfig.from_environment({"BOT_TOKEN": support.TOKEN,
            "DOMAIN": "bot-1791715412-5398-ggsell.bothost.tech"})
        self.assertEqual(first.webapp_url, second.webapp_url)
        self.assertEqual(first.webhook_secret, second.webhook_secret)
        self.assertNotEqual(first.webhook_secret, main.automatic_webhook_secret("another:bot-token"))
        # A numeric Telegram ID or an unrecognized identifier is not a domain.
        for bot_id in ("123456", "../../other", "bot_user/attacker", ""):
            self.assertEqual(main.RuntimeConfig.from_environment({"BOT_ID": bot_id}).webapp_url, "")

    def test_explicit_settings_remain_compatible(self):
        config = main.RuntimeConfig.from_environment({"BOT_TOKEN": support.TOKEN,
            "DOMAIN": "bot-123.bothost.tech", "WEBAPP_URL": "https://mining.example.org/",
            "TELEGRAM_WEBHOOK_SECRET": "my_existing_secret", "DATABASE_PATH": "/data/existing.sqlite3",
            "PORT": "8000", "REGISTER_WEBHOOK": "0"})
        self.assertEqual(config.webapp_url, "https://mining.example.org")
        self.assertEqual(config.webhook_secret, "my_existing_secret")
        self.assertEqual(config.db_path, "/data/existing.sqlite3")
        self.assertEqual(config.port, 8000)
        self.assertFalse(config.register_webhook)
        server = SimpleNamespace(register_webhook=False)
        with patch("main.threading.Thread") as worker:
            self.assertIsNone(main.start_telegram_setup(server))
            worker.assert_not_called()

    def test_bad_domain_does_not_become_a_webhook(self):
        for domain in ("", "localhost", "https://user:pass@site.example.org", "https://site.example.org/?token=secret",
                       "https://site.example.org/#fragment", "https://site.example.org\\evil", "bad domain",
                       "ftp://site.example.org", "bot_123.bothost.tech"):
            with self.subTest(domain=domain):
                config = main.RuntimeConfig.from_environment({"BOT_TOKEN": support.TOKEN, "DOMAIN": domain})
                self.assertEqual(config.webapp_url, "")
                with patch("main.telegram_api") as api:
                    with self.assertRaises(ValueError):
                        main.register_webhook(config)
                    api.assert_not_called()

    def test_automatic_startup_and_authenticated_requests_use_the_same_settings(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {}, clear=True):
            config = main.RuntimeConfig.from_environment({"BOT_TOKEN": support.TOKEN,
                "DOMAIN": "bot-1791715412-5398-ggsell.bothost.tech"})
            db_path = str(Path(folder) / "accounts.sqlite3")
            server = main.GameServer(("127.0.0.1", 0), db_path=db_path, config=config)
            http_worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            http_worker.start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                with patch("main.telegram_api", return_value={"ok": True}) as api, contextlib.redirect_stdout(io.StringIO()):
                    worker = main.start_telegram_setup(server)
                    worker.join(timeout=2)
                    self.assertFalse(worker.is_alive())
                    self.assertTrue(server.telegram_registered)
                    webhook, menu = api.call_args_list
                    self.assertEqual(webhook.args, ("setWebhook", {
                        "url": config.webapp_url + "/telegram/webhook", "secret_token": server.webhook_secret,
                        "allowed_updates": ["message", "callback_query"]}, support.TOKEN))
                    self.assertEqual(menu.args[1]["menu_button"]["web_app"]["url"], config.webapp_url)
                    with urllib.request.urlopen(url + "/health", timeout=2) as response:
                        self.assertTrue(json.load(response)["telegram_ready"])
                    request = urllib.request.Request(url + "/api/me", headers={"X-Telegram-Init-Data": support.signed_data()})
                    with urllib.request.urlopen(request, timeout=2) as response:
                        self.assertEqual(json.load(response)["user"]["id"], 42)
                    with self.assertRaises(urllib.error.HTTPError) as rejected:
                        urllib.request.urlopen(url + "/api/me", timeout=2)
                    self.assertEqual(rejected.exception.code, 401)
                    body = json.dumps({"message": {"chat": {"id": 42, "type": "private"},
                        "from": {"id": 42}, "text": "/start"}}).encode()
                    request = urllib.request.Request(url + "/telegram/webhook", data=body, headers={
                        "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": config.webhook_secret,
                        "Host": "attacker.example.org"})
                    with urllib.request.urlopen(request, timeout=2) as response:
                        self.assertEqual(response.status, 200)
                    self.assertEqual(api.call_args.args[0], "sendPhoto")
                    self.assertEqual(api.call_args.args[1]["photo"], config.webapp_url + main.WELCOME_BANNER_PATH)
                    self.assertEqual(server.webapp_url, config.webapp_url)
                    request = urllib.request.Request(url + "/telegram/webhook", data=body)
                    with self.assertRaises(urllib.error.HTTPError) as rejected:
                        urllib.request.urlopen(request, timeout=2)
                    self.assertEqual(rejected.exception.code, 403)
                with server.db() as conn:
                    conn.execute("UPDATE users SET balance=123400 WHERE telegram_id=42")
            finally:
                server.shutdown()
                server.server_close()
                http_worker.join(timeout=2)
            restarted = main.GameServer(("127.0.0.1", 0), db_path=db_path, config=config)
            try:
                self.assertEqual(restarted.webhook_secret, server.webhook_secret)
                with restarted.db() as conn:
                    self.assertEqual(conn.execute("SELECT balance FROM users WHERE telegram_id=42").fetchone()[0], 123400)
            finally:
                restarted.server_close()

    def test_registration_retries_transient_errors_and_obeys_telegram_delay(self):
        config = main.RuntimeConfig.from_environment({"BOT_TOKEN": support.TOKEN, "DOMAIN": "bot-123.bothost.tech"})
        server = SimpleNamespace(bot_token=config.bot_token, webapp_url=config.webapp_url,
            webhook_secret=config.webhook_secret, telegram_registered=False,
            stop_event=Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False)))
        limited = urllib.error.HTTPError("https://api.telegram.org", 429, "rate limited", {},
            io.BytesIO(b'{"parameters":{"retry_after":75}}'))
        with patch("main.telegram_api", side_effect=[TimeoutError(), limited, {"ok": True}, {"ok": True}]) as api:
            with contextlib.redirect_stdout(io.StringIO()):
                main.register_webhook_in_background(server)
        self.assertEqual([call.args for call in server.stop_event.wait.call_args_list], [(2,), (75,)])
        self.assertTrue(server.telegram_registered)
        self.assertEqual(api.call_count, 4)

    def test_missing_credentials_do_not_attempt_telegram_calls_or_invent_a_token(self):
        config = main.RuntimeConfig.from_environment({})
        self.assertEqual((config.bot_token, config.webapp_url, config.webhook_secret), ("", "", ""))
        server = SimpleNamespace(bot_token="", webapp_url="")
        with patch("main.telegram_api") as api, contextlib.redirect_stdout(io.StringIO()):
            main.register_webhook_in_background(server)
            api.assert_not_called()
        configured = SimpleNamespace(bot_token=support.TOKEN, webapp_url="https://bot-123.bothost.tech",
            webhook_secret="secret", telegram_registered=False, stop_event=Mock(is_set=Mock(return_value=False)))
        error = urllib.error.HTTPError("https://api.telegram.org", 401, "Unauthorized", {}, None)
        with patch("main.telegram_api", side_effect=error) as api, contextlib.redirect_stdout(io.StringIO()) as log:
            main.register_webhook_in_background(configured)
        api.assert_called_once()
        configured.stop_event.wait.assert_not_called()
        self.assertNotIn(support.TOKEN, log.getvalue())
        self.assertFalse(configured.telegram_registered)


if __name__ == "__main__":
    unittest.main()
