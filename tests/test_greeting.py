import hashlib
import io
import json
import unittest
import urllib.error
from html.parser import HTMLParser
from unittest.mock import patch

import main
import test_main as support


class CaptionParser(HTMLParser):
    def __init__(self, caption):
        super().__init__()
        self.text = ""
        self.bold = []
        self.emoji_ids = []
        self.in_bold = False
        self.feed(caption)

    def handle_starttag(self, tag, attrs):
        if tag == "b":
            self.in_bold = True
        elif tag == "tg-emoji":
            self.emoji_ids.append(dict(attrs)["emoji-id"])

    def handle_endtag(self, tag):
        if tag == "b":
            self.in_bold = False

    def handle_data(self, value):
        self.text += value
        if self.in_bold:
            self.bold.append(value)


class GreetingTests(unittest.TestCase):
    setUp = support.PickaxeAppTests.setUp
    tearDown = support.PickaxeAppTests.tearDown
    call = support.PickaxeAppTests.call

    def webhook(self, text, *, chat_type="private"):
        payload = {"message": {"chat": {"id": 42, "type": chat_type},
                              "from": {"id": 42, "first_name": "Игрок"}, "text": text}}
        return self.call("/telegram/webhook", payload, auth=False,
                         extra_headers="X-Telegram-Bot-Api-Secret-Token: test_secret\r\n")

    def test_start_sends_banner_formatted_caption_and_green_mini_app_button(self):
        self.server.webapp_url = "https://bot-123.bothost.tech/"
        with patch("main.telegram_api", return_value={"ok": True}) as api:
            status, result = self.webhook("/start@ShaxterBot invite_42")
        self.assertEqual((status, result), (200, {"ok": True}))
        api.assert_called_once()
        method, payload, token = api.call_args.args
        self.assertEqual((method, token), ("sendPhoto", support.TOKEN))
        self.assertEqual(payload["chat_id"], 42)
        self.assertEqual(payload["photo"], "https://bot-123.bothost.tech/static/assets/welcome-banner.png")
        self.assertEqual(payload["parse_mode"], "HTML")
        caption = CaptionParser(payload["caption"])
        self.assertEqual(caption.text,
            "Добро пожаловать 👋,\n\n"
            "чтобы начать игру и получить бонус в виде бесплатного колеса фортуны 🎡 "
            "с выигрышем от 500₽ до 5000₽, заходите в Mini App и начинайте игру!")
        self.assertEqual(caption.bold, ["Добро пожаловать", "500₽", "5000₽", "Mini App", "игру"])
        self.assertEqual(caption.emoji_ids, ["5440660757194744323", "5415683280395585071"])
        self.assertLessEqual(len(caption.text.encode("utf-16-le")) // 2, 1024)
        rows = payload["reply_markup"]["inline_keyboard"]
        self.assertEqual(rows[0][0]["url"], "https://t.me/Game_shaxter_support")
        self.assertEqual(rows[-1], [{"text": "⚒️Играть", "style": "success",
                                   "web_app": {"url": "https://bot-123.bothost.tech"}}])

    def test_custom_emoji_rejection_keeps_banner_bold_and_green_button(self):
        self.server.webapp_url = "https://bot-123.bothost.tech"
        rejection = urllib.error.HTTPError("https://api.telegram.org", 400, "Bad Request", {},
            io.BytesIO(json.dumps({"ok": False, "description": "Bad Request: CUSTOM_EMOJI_NOT_ALLOWED"}).encode()))
        with patch("main.telegram_api", side_effect=[rejection, {"ok": True}]) as api:
            self.assertEqual(self.webhook("/start")[0], 200)
        self.assertEqual(api.call_count, 2)
        first, retry = [call.args[1] for call in api.call_args_list]
        caption = CaptionParser(retry["caption"])
        self.assertEqual(caption.emoji_ids, [])
        self.assertEqual(caption.bold, ["Добро пожаловать", "500₽", "5000₽", "Mini App", "игру"])
        self.assertEqual(caption.text, CaptionParser(first["caption"]).text)
        self.assertEqual(retry["photo"], first["photo"])
        self.assertEqual(retry["reply_markup"], first["reply_markup"])

    def test_timeout_or_unrelated_rejection_is_not_retried(self):
        self.server.webapp_url = "https://bot-123.bothost.tech"
        for error in (TimeoutError("timeout"),
                      urllib.error.HTTPError("https://api.telegram.org", 400, "Bad Request", {},
                          io.BytesIO(b'{"description":"Bad Request: wrong file identifier/HTTP URL specified"}'))):
            with self.subTest(error=type(error).__name__), patch("main.telegram_api", side_effect=error) as api:
                self.assertEqual(self.webhook("/start")[0], 502)
                api.assert_called_once()

    def test_support_uses_its_own_contact(self):
        with patch("main.MANAGER_USERNAME", "Other_Manager"), patch("main.telegram_api") as api:
            self.assertEqual(self.webhook("/support")[0], 200)
        method, payload, _ = api.call_args.args
        self.assertEqual(method, "sendMessage")
        self.assertEqual(payload["text"], "Поддержка: @Game_shaxter_support")
        self.assertEqual(payload["reply_markup"]["inline_keyboard"][0][0]["url"],
                         "https://t.me/Game_shaxter_support")

    def test_start_only_handles_the_command_in_private_chat_with_https_url(self):
        for command, chat_type, url in (("/starter", "private", "https://bot-123.bothost.tech"),
                                         ("/start", "group", "https://bot-123.bothost.tech"),
                                         ("/start", "private", "http://localhost:3000")):
            with self.subTest(command=command, chat_type=chat_type, url=url), patch("main.telegram_api") as api:
                self.server.webapp_url = url
                self.assertEqual(self.webhook(command, chat_type=chat_type)[0], 200)
                api.assert_not_called()

    def test_banner_is_available_without_telegram_auth(self):
        raw = b"GET /static/assets/welcome-banner.png HTTP/1.1\r\nHost: localhost\r\n\r\n"
        connection = support.MemoryConnection(raw)
        main.GameHandler(connection, ("127.0.0.1", 54321), self.server)
        header, body = connection.output.getvalue().split(b"\r\n\r\n", 1)
        self.assertIn(b"200 OK", header)
        self.assertIn(b"Content-Type: image/png", header)
        self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(hashlib.sha256(body).digest(),
                         hashlib.sha256((main.STATIC_DIR / "assets/welcome-banner.png").read_bytes()).digest())
        self.assertLess(len(body), 10 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
