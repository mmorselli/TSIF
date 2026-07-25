import json
import logging
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

from arklogin import (
    ArkLoginBot,
    AttemptTracker,
    BackRecovery,
    ClickResult,
    GameWindow,
    OCRDetection,
    Recognition,
    ScreenReader,
    canonical,
    configure_logging,
    load_config,
    load_language,
    relative_click_point,
)


ROOT = Path(__file__).resolve().parents[1]


def make_reader(server_number="6448"):
    reader = ScreenReader.__new__(ScreenReader)
    reader.minimum_confidence = 0.45
    reader.server_number = server_number
    reader.set_language(load_language(ROOT / "lang.json.example"))
    return reader


class TextRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.reader = make_reader()

    def test_canonical(self):
        self.assertEqual(canonical("EU-PVE-GenOne 6448"), "EUPVEGENONE6448")
        self.assertEqual(canonical("Échec réseau"), "ÉCHECRÉSEAU")
        self.assertEqual(canonical("E\u0301chec"), "ÉCHEC")

    def test_custom_language_aliases(self):
        language = dict(self.reader.language)
        language["join_game"] = ("PLAY ONLINE",)
        self.reader.set_language(language)

        result = self.reader.classify_text(["PLAY ONLINE"])
        anchors = self.reader.action_anchors(
            (OCRDetection("PLAY ONLINE", 0.99, (0.2, 0.7)),)
        )

        self.assertEqual(result.state, "home")
        self.assertEqual(anchors["join_game"], (0.2, 0.7))

    def test_home(self):
        result = self.reader.classify_text(["JOIN GAME", "CREATE OR RESUME GAME"])
        self.assertEqual(result.state, "home")

    def test_start(self):
        result = self.reader.classify_text(
            ["PRESS", "TO START", "JOIN LAST SESSION", "NEWS"]
        )
        self.assertEqual(result.state, "start")

    def test_server_list_and_number(self):
        result = self.reader.classify_text(
            ["MULTIPLAYER SERVERS: 1406", "EU-PVE-GenOne6448", "JOIN"]
        )
        self.assertEqual(result.state, "server_list")
        self.assertTrue(result.target_server_found)

    def test_server_number_does_not_match_longer_numeric_id(self):
        for wrong_id in ("16448", "64480"):
            with self.subTest(wrong_id=wrong_id):
                result = self.reader.classify_text(
                    ["MULTIPLAYER SERVERS: 1", f"EU-PVE-GenOne{wrong_id}", "JOIN"]
                )
                self.assertFalse(result.target_server_found)

    def test_connecting_does_not_look_like_list_ready(self):
        result = self.reader.classify_text(
            ["MULTIPLAYER SERVERS: 0", "Joining server EU-PVE-GenOne6448"]
        )
        self.assertEqual(result.state, "connecting")

    def test_connection_failed_has_priority(self):
        result = self.reader.classify_text(
            ["MULTIPLAYER SERVERS: 0", "CONNECTION FAILED", "CANCEL"]
        )
        self.assertEqual(result.state, "connection_failed")

    def test_network_failure(self):
        result = self.reader.classify_text(
            ["NETWORK FAILURE", "MESSAGE", "Server full.", "ACCEPT", "PRESS TO START"]
        )
        self.assertEqual(result.state, "network_failure")

    def test_joining_failed(self):
        result = self.reader.classify_text(
            [
                "MULTIPLAYER SERVERS: 0",
                "JOINING FAILED",
                "Unknown Error",
                "OK",
            ]
        )
        self.assertEqual(result.state, "joining_failed")
        title_only = self.reader.classify_text(
            ["MULTIPLAYER SERVERS: 0", "JOINING FAILED"]
        )
        self.assertEqual(title_only.state, "joining_failed")

    def test_dlc_packs(self):
        result = self.reader.classify_text(["DLC OWNED (8/9)", "BACK"])
        self.assertEqual(result.state, "dlc_packs")

    def test_event(self):
        result = self.reader.classify_text(
            ["EU-PVE-GENONE6448", "REQUIRED MODS", "JOIN"]
        )
        self.assertEqual(result.state, "event")
        self.assertTrue(result.target_server_found)


class ConfigTests(unittest.TestCase):
    def test_invalid_server_number_is_rejected(self):
        config = {
            "server_number": "server-6448",
            "event_screen_enabled": True,
            "window_title_contains": "Ark: Survival Ascended",
            "process_name": "ArkAscended",
            "poll_interval_seconds": 1.5,
            "action_cooldown_seconds": 3,
            "post_cancel_wait_seconds": 2,
            "ocr_min_confidence": 0.45,
            "click_positions": {
                "start": [0.5, 0.8],
                "join_game": [0.2, 0.7],
                "join_server": [0.9, 0.8],
                "join_event": [0.2, 0.9],
                "cancel": [0.5, 0.7],
                "back": [0.1, 0.8],
                "accept_network_failure": [0.5, 0.7],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "digits only"):
                load_config(path)

    def test_missing_language_entries_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lang.json"
            path.write_text(json.dumps({"join": ["JOIN"]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Missing language entries"):
                load_language(path)

    def test_log_file_enabled_is_optional_and_must_be_boolean(self):
        config = json.loads(
            (ROOT / "config.json.example").read_text(encoding="utf-8")
        )
        config.pop("log_file_enabled")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            self.assertTrue(load_config(path)["log_file_enabled"])

            config["log_file_enabled"] = "false"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError, "log_file_enabled must be true or false"
            ):
                load_config(path)

    def test_file_logging_can_be_disabled(self):
        logger = configure_logging(file_enabled=False)
        try:
            self.assertFalse(
                any(
                    isinstance(handler, RotatingFileHandler)
                    for handler in logger.handlers
                )
            )
        finally:
            for handler in logger.handlers[:]:
                logger.removeHandler(handler)
                handler.close()


class CoordinateTests(unittest.TestCase):
    def setUp(self):
        self.reader = make_reader()

    def test_extra_client_height_does_not_move_bottom_buttons(self):
        reference = relative_click_point((210, 193, 1920, 1152), [0.895, 0.875])
        taller = relative_click_point((210, 193, 1920, 1200), [0.895, 0.875])
        self.assertEqual(reference, taller)
        self.assertEqual(taller, (1928, 1201))

    def test_ocr_anchor_uses_exact_join_not_join_last_played(self):
        anchors = self.reader.action_anchors(
            (
                OCRDetection("JOIN LAST PLAYED", 0.99, (0.88, 0.80)),
                OCRDetection("JOIN", 0.96, (0.25, 0.74)),
            )
        )
        self.assertEqual(anchors["join_event"], (0.25, 0.74))
        self.assertEqual(anchors["join_server"], (0.25, 0.74))

    def test_ocr_anchor_finds_joining_failed_ok(self):
        anchors = self.reader.action_anchors(
            (OCRDetection("OK", 0.99, (0.5, 0.63)),)
        )
        self.assertEqual(anchors["dismiss_joining_failed"], (0.5, 0.63))

    def test_join_game_anchor_outside_card_is_rejected(self):
        anchors = self.reader.action_anchors(
            (OCRDetection("JOIN GAME", 0.99, (0.25, 0.86)),)
        )
        self.assertNotIn("join_game", anchors)

    def test_ocr_anchor_finds_dlc_back(self):
        anchors = self.reader.action_anchors(
            (OCRDetection("BACK", 0.99, (0.5, 0.85)),)
        )
        self.assertEqual(anchors["back_dlc"], (0.5, 0.85))


class FlowTests(unittest.TestCase):
    def test_server_full_returns_through_start_to_join_game(self):
        bot = ArkLoginBot.__new__(ArkLoginBot)
        bot.config = {
            "server_number": "6448",
            "event_screen_enabled": True,
            "post_cancel_wait_seconds": 2,
        }
        bot.logger = logging.getLogger("arklogin.flow-test")
        bot.now = lambda: 0.0
        bot.attempt = AttemptTracker()
        bot.back_recovery = BackRecovery(
            phase="wait_back_exit",
            started_at=0.0,
            cancel_sent_at=0.0,
            back_sent_at=0.0,
        )
        bot.last_state = None
        actions = []
        bot.perform_action = (
            lambda _recognition, _window, _bounds, name, _description: (
                actions.append(name) or ClickResult.SENT
            )
        )
        bot.notice = lambda *_args, **_kwargs: None

        window = GameWindow(hwnd=1, pid=1, title="Ark: Survival Ascended")
        bounds = (0, 0, 1920, 1152)
        states = (
            Recognition(
                "joining_failed",
                "",
                False,
                ("JOINING FAILED", "Unknown Error", "OK"),
            ),
            Recognition(
                "server_list",
                "",
                False,
                ("MULTIPLAYER SERVERS: 0",),
            ),
            Recognition("network_failure", "", False, ("NETWORK FAILURE", "ACCEPT")),
            Recognition("start", "", False, ("PRESS", "TO START")),
            Recognition("home", "", False, ("JOIN GAME",)),
        )

        for state in states:
            bot.handle(state, window, bounds)

        self.assertEqual(
            actions,
            [
                "dismiss_joining_failed",
                "accept_network_failure",
                "start",
                "join_game",
            ],
        )

    def test_dlc_screen_returns_to_home(self):
        bot = ArkLoginBot.__new__(ArkLoginBot)
        bot.config = {
            "server_number": "6448",
            "event_screen_enabled": True,
            "post_cancel_wait_seconds": 2,
        }
        bot.logger = logging.getLogger("arklogin.dlc-flow-test")
        bot.now = lambda: 0.0
        bot.attempt = AttemptTracker()
        bot.back_recovery = BackRecovery(
            phase="wait_back_exit",
            started_at=0.0,
            cancel_sent_at=0.0,
            back_sent_at=0.0,
        )
        bot.last_state = None
        actions = []
        bot.perform_action = (
            lambda _recognition, _window, _bounds, name, _description: (
                actions.append(name) or ClickResult.SENT
            )
        )
        bot.notice = lambda *_args, **_kwargs: None

        window = GameWindow(hwnd=1, pid=1, title="Ark: Survival Ascended")
        bounds = (0, 0, 1920, 1152)
        bot.handle(
            Recognition(
                "dlc_packs",
                "",
                False,
                ("DLC OWNED (8/9)", "BACK"),
                {"back_dlc": (0.5, 0.85)},
            ),
            window,
            bounds,
        )
        bot.handle(
            Recognition(
                "home",
                "",
                False,
                ("JOIN GAME",),
                {"join_game": (0.24, 0.69)},
            ),
            window,
            bounds,
        )

        self.assertEqual(actions, ["back_dlc", "join_game"])


if __name__ == "__main__":
    unittest.main()
