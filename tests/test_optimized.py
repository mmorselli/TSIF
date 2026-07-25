import logging
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import arklogin
from arklogin import (
    ArkLoginBot,
    AttemptTracker,
    BACK_PROFILE,
    BackRecovery,
    ClickResult,
    EVENT_PROFILE,
    GameWindow,
    HOME_PROFILE,
    JOINING_FAILED_PROFILE,
    OCRDetection,
    OCRProfile,
    OUTCOME_PROFILE,
    Recognition,
    SERVER_PROFILE,
    START_PROFILE,
    ScreenReader,
    load_config,
    reference_frame,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeWindowManager:
    def __init__(self, window=None, bounds=(0, 0, 1920, 1152)):
        self.window = window or GameWindow(1, 10, "Ark: Survival Ascended")
        self.bounds = bounds
        self.activate_calls = 0

    def find(self):
        return self.window

    def client_bounds(self, _window):
        return self.bounds

    def activate(self, _window):
        self.activate_calls += 1
        return True


def make_bot(clock=None):
    clock = clock or FakeClock()
    bot = ArkLoginBot.__new__(ArkLoginBot)
    bot.config = load_config(ROOT / "config.json")
    bot.logger = logging.getLogger(f"arklogin.test.{id(bot)}")
    bot.logger.addHandler(logging.NullHandler())
    bot.dry_run = True
    bot.max_actions = None
    bot.now = clock
    bot.sleep = lambda _seconds: None
    bot.actions = 0
    bot.back_recovery = None
    bot.last_sent_by_action = {}
    bot.last_state = None
    bot.last_notice = ("", 0.0)
    bot.active_profiles = ()
    bot.profile_misses = 0
    bot.last_full_scan_time = float("-inf")
    bot.last_frame_size = None
    bot.cached_signature = None
    bot.cached_profile_key = None
    bot.cached_recognition = None
    bot.cached_ocr_time = float("-inf")
    bot.next_scan_at = 0.0
    bot.background_since = None
    bot.next_focus_attempt_at = None
    bot.attempt = AttemptTracker()
    bot.window_manager = FakeWindowManager()
    return bot


class InputTimingTests(unittest.TestCase):
    def test_debounce_applies_only_to_same_action(self):
        clock = FakeClock()
        bot = make_bot(clock)
        window = bot.window_manager.window
        bounds = bot.window_manager.bounds

        first = bot.click(window, bounds, "join_server", "JOIN")
        repeated = bot.click(window, bounds, "join_server", "JOIN")
        different = bot.click(window, bounds, "join_event", "JOIN event")
        clock.advance(bot.config["same_action_retry_seconds"] + 0.01)
        retried = bot.click(window, bounds, "join_server", "JOIN")

        self.assertIs(first, ClickResult.SENT)
        self.assertIs(repeated, ClickResult.DEBOUNCED)
        self.assertIs(different, ClickResult.SENT)
        self.assertIs(retried, ClickResult.SENT)

    def test_stale_bounds_do_not_count_as_sent(self):
        bot = make_bot()
        bot.dry_run = False
        bot.window_manager.bounds = (10, 10, 1280, 720)
        with patch("arklogin.win32gui.GetForegroundWindow", return_value=1):
            result = bot.click(
                bot.window_manager.window,
                (0, 0, 1920, 1152),
                "join_server",
                "JOIN",
            )
        self.assertIs(result, ClickResult.STALE)
        self.assertEqual(bot.actions, 0)
        self.assertNotIn("join_server", bot.last_sent_by_action)

    def test_new_join_does_not_reuse_connecting_from_previous_attempt(self):
        bot = make_bot()
        bot.attempt.active = True
        bot.attempt.saw_connecting = True
        bot.attempt.unknown_samples = 4
        bot.attempt.pause_until = 50.0
        recognition = Recognition(
            "server_list",
            "",
            True,
            ("MULTIPLAYER SERVERS", "6468", "JOIN"),
            {"join_server": (0.9, 0.9)},
        )

        result = bot.perform_action(
            recognition,
            bot.window_manager.window,
            bot.window_manager.bounds,
            "join_server",
            "JOIN",
        )

        self.assertIs(result, ClickResult.SENT)
        self.assertTrue(bot.attempt.active)
        self.assertFalse(bot.attempt.saw_connecting)
        self.assertEqual(bot.attempt.unknown_samples, 0)
        self.assertEqual(bot.attempt.pause_until, 0.0)

    def test_keyboard_interrupt_releases_button_and_restores_cursor(self):
        bot = make_bot()
        bot.dry_run = False
        sleep_calls = 0

        def interrupt_during_hold(_seconds):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls == 2:
                raise KeyboardInterrupt()

        bot.sleep = interrupt_during_hold
        mouse_events = []
        cursor_positions = []
        with (
            patch("arklogin.win32gui.GetForegroundWindow", return_value=1),
            patch("arklogin.win32api.GetCursorPos", return_value=(12, 34)),
            patch(
                "arklogin.win32api.SetCursorPos",
                side_effect=lambda value: cursor_positions.append(value),
            ),
            patch(
                "arklogin.win32api.mouse_event",
                side_effect=lambda event, *_args: mouse_events.append(event),
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                bot.click(
                    bot.window_manager.window,
                    bot.window_manager.bounds,
                    "join_server",
                    "JOIN",
                )

        self.assertEqual(
            mouse_events,
            [arklogin.win32con.MOUSEEVENTF_LEFTDOWN, arklogin.win32con.MOUSEEVENTF_LEFTUP],
        )
        self.assertEqual(cursor_positions[-1], (12, 34))


class SchedulerTests(unittest.TestCase):
    def test_focus_is_not_reacquired_before_five_seconds(self):
        clock = FakeClock(10.0)
        bot = make_bot(clock)
        bot.grab = lambda _window: None

        with patch("arklogin.win32gui.GetForegroundWindow", return_value=0):
            bot.step()
            clock.advance(4.9)
            bot.step()
            self.assertEqual(bot.window_manager.activate_calls, 0)
            clock.advance(0.1)
            bot.step()

        self.assertEqual(bot.window_manager.activate_calls, 1)

    def test_success_pause_never_reacquires_focus(self):
        clock = FakeClock(10.0)
        bot = make_bot(clock)
        bot.attempt.pause_until = 20.0

        with patch("arklogin.win32gui.GetForegroundWindow", return_value=0):
            bot.step()
            clock.advance(9.9)
            bot.step()

        self.assertEqual(bot.window_manager.activate_calls, 0)

    def test_run_does_not_swallow_keyboard_interrupt(self):
        bot = make_bot()
        bot.step = lambda: 1.0
        bot.sleep = lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            bot.run()


class RecoveryTests(unittest.TestCase):
    def test_cancel_is_not_repeated_and_back_waits_for_ready_state(self):
        clock = FakeClock()
        bot = make_bot(clock)
        sent = []

        def perform(_recognition, _window, _bounds, name, _description):
            sent.append(name)
            return ClickResult.SENT

        bot.perform_action = perform
        bot.back_recovery = BackRecovery("wait_back_ready", 0.0, 0.0)
        failed = Recognition("connection_failed", "", False, ("CANCEL",))
        connecting = Recognition("connecting", "", True, ("BACK",))

        self.assertTrue(
            bot.handle_back_recovery(
                failed, bot.window_manager.window, bot.window_manager.bounds
            )
        )
        self.assertEqual(sent, [])

        clock.advance(bot.config["post_cancel_wait_seconds"] - 0.01)
        bot.handle_back_recovery(
            connecting, bot.window_manager.window, bot.window_manager.bounds
        )
        self.assertEqual(sent, [])

        clock.advance(0.02)
        bot.handle_back_recovery(
            connecting, bot.window_manager.window, bot.window_manager.bounds
        )
        self.assertEqual(sent, ["back"])
        self.assertEqual(bot.back_recovery.phase, "wait_back_exit")

    def test_home_clears_recovery_and_is_not_consumed(self):
        bot = make_bot()
        bot.back_recovery = BackRecovery("wait_back_exit", 0.0, 0.0, 0.5)
        home = Recognition("home", "", False, ("JOIN GAME",))
        consumed = bot.handle_back_recovery(
            home, bot.window_manager.window, bot.window_manager.bounds
        )
        self.assertFalse(consumed)
        self.assertIsNone(bot.back_recovery)

    def test_timeout_while_connecting_keeps_recovery_and_retries_back(self):
        clock = FakeClock(20.0)
        bot = make_bot(clock)
        sent = []
        bot.perform_action = (
            lambda _recognition, _window, _bounds, name, _description: (
                sent.append(name) or ClickResult.SENT
            )
        )
        bot.back_recovery = BackRecovery("wait_back_exit", 0.0, 0.0, 1.0)
        connecting = Recognition("connecting", "", True, ("BACK",))

        consumed = bot.handle_back_recovery(
            connecting, bot.window_manager.window, bot.window_manager.bounds
        )

        self.assertTrue(consumed)
        self.assertEqual(sent, ["back"])
        self.assertIsNotNone(bot.back_recovery)
        self.assertEqual(bot.back_recovery.started_at, 20.0)

    def test_safe_state_wins_over_expired_recovery(self):
        clock = FakeClock(20.0)
        bot = make_bot(clock)
        bot.back_recovery = BackRecovery("wait_back_exit", 0.0, 0.0, 1.0)
        start = Recognition("start", "", False, ("PRESS", "TO START"))

        consumed = bot.handle_back_recovery(
            start, bot.window_manager.window, bot.window_manager.bounds
        )

        self.assertFalse(consumed)
        self.assertIsNone(bot.back_recovery)


class SuccessDetectionTests(unittest.TestCase):
    def test_success_requires_connecting_and_two_unknown_samples(self):
        clock = FakeClock()
        bot = make_bot(clock)
        bot.attempt.active = True

        bot.track_attempt(Recognition("unknown", "", False, ()))
        self.assertFalse(bot.attempt.saw_connecting)
        self.assertEqual(bot.attempt.pause_until, 0.0)

        bot.track_attempt(Recognition("connecting", "", True, ()))
        self.assertTrue(bot.attempt.saw_connecting)
        bot.track_attempt(Recognition("unknown", "", False, ()))
        self.assertEqual(bot.attempt.pause_until, 0.0)

        clock.advance(bot.config["success_unknown_confirm_seconds"] + 0.01)
        bot.track_attempt(Recognition("unknown", "", False, ()))
        self.assertGreater(bot.attempt.pause_until, clock())

    def test_cached_unknowns_do_not_count_as_new_samples(self):
        clock = FakeClock()
        bot = make_bot(clock)
        bot.attempt.active = True
        bot.track_attempt(Recognition("connecting", "", True, ()))

        bot.track_attempt(Recognition("unknown", "", False, ()), fresh=True)
        for _ in range(20):
            clock.advance(0.2)
            bot.track_attempt(Recognition("unknown", "", False, ()), fresh=False)

        self.assertEqual(bot.attempt.unknown_samples, 1)
        self.assertEqual(bot.attempt.pause_until, 0.0)
        bot.track_attempt(Recognition("unknown", "", False, ()), fresh=True)
        self.assertGreater(bot.attempt.pause_until, clock())

    def test_return_to_server_list_after_connecting_cancels_success_tracking(self):
        bot = make_bot()
        bot.attempt.active = True
        bot.track_attempt(Recognition("connecting", "", True, ()))

        bot.track_attempt(Recognition("server_list", "", False, ("JOIN",)))

        self.assertFalse(bot.attempt.active)
        self.assertFalse(bot.attempt.saw_connecting)


class OCRProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = ScreenReader(0.45, "6468")

    def test_profile_mask_keeps_canvas_and_does_not_modify_input(self):
        frame = np.full((1200, 1920, 3), 255, dtype=np.uint8)
        original = frame.copy()
        prepared = self.reader._prepare(frame)
        masked = self.reader.prepare(frame, HOME_PROFILE)
        self.assertEqual(masked.shape, prepared.shape)
        self.assertTrue(np.array_equal(frame, original))
        self.assertEqual(int(masked[0, 0, 0]), 0)
        self.assertEqual(int(masked[500, 300, 0]), 255)

    def test_server_number_outside_first_row_is_ignored(self):
        first = OCRDetection("EU-PVE-GenOne6468", 0.99, (0.20, 0.33))
        lower = OCRDetection("EU-PVE-GenOne6468", 0.99, (0.20, 0.55))
        self.assertTrue(self.reader._server_is_first((first,), 1920, 1152))
        self.assertFalse(self.reader._server_is_first((lower,), 1920, 1152))

    def test_observed_different_first_row_is_credible_absence(self):
        observed_absence = Recognition(
            "server_list",
            "",
            False,
            ("EU-PVE-AnotherServer", "JOIN"),
            {"join_server": (0.9, 0.9)},
            first_server_row_observed=True,
        )
        missing_row = Recognition(
            "server_list",
            "",
            False,
            ("MULTIPLAYER SERVERS", "JOIN"),
            {"join_server": (0.9, 0.9)},
        )
        self.assertTrue(
            self.reader._profile_result_is_credible(
                observed_absence, SERVER_PROFILE
            )
        )
        self.assertFalse(
            self.reader._profile_result_is_credible(missing_row, SERVER_PROFILE)
        )

    def test_mask_cache_is_bounded_during_resize(self):
        for width in range(900, 930):
            self.reader._profile_mask(HOME_PROFILE, width, 600)
        self.assertLessEqual(len(self.reader.mask_cache), arklogin.MASK_CACHE_LIMIT)

    def test_profiles_recognize_reference_states_and_anchors(self):
        cases = (
            ("00_start_screen.png", START_PROFILE, "start", "start", False),
            ("01_first_screen.png", HOME_PROFILE, "home", "join_game", False),
            (
                "02_join-server.png",
                SERVER_PROFILE,
                "server_list",
                "join_server",
                True,
            ),
            (
                "03_optional_event.png",
                EVENT_PROFILE,
                "event",
                "join_event",
                True,
            ),
            (
                "04_connection_failed.png",
                OUTCOME_PROFILE,
                "connection_failed",
                "cancel",
                False,
            ),
            (
                "05_cancel_after_connection_failed.png",
                BACK_PROFILE,
                "connecting",
                "back",
                True,
            ),
            (
                "06_server_full_press_accept.png",
                OUTCOME_PROFILE,
                "network_failure",
                "accept_network_failure",
                False,
            ),
        )
        for filename, profile, state, anchor, target in cases:
            with self.subTest(filename=filename, profile=profile.name):
                result = self.reader.recognize(
                    reference_frame(ROOT / "docs" / filename), profile
                )
                self.assertEqual(result.state, state)
                self.assertIn(anchor, result.anchors)
                self.assertEqual(result.target_server_found, target)

    def test_server_and_event_profiles_survive_small_window_resize(self):
        cases = (
            ("02_join-server.png", SERVER_PROFILE, "server_list", "join_server"),
            ("03_optional_event.png", EVENT_PROFILE, "event", "join_event"),
        )
        for filename, profile, expected_state, anchor in cases:
            frame = reference_frame(ROOT / "docs" / filename)
            height = round(frame.shape[0] * 800 / frame.shape[1])
            resized = arklogin.cv2.resize(
                frame,
                (800, height),
                interpolation=arklogin.cv2.INTER_AREA,
            )
            with self.subTest(filename=filename, width=800):
                result = self.reader.recognize(resized, profile)
                self.assertEqual(result.state, expected_state)
                self.assertTrue(result.target_server_found)
                self.assertIn(anchor, result.anchors)

    def test_modal_guard_has_priority_over_underlying_server_screen(self):
        result = self.reader.recognize(
            reference_frame(ROOT / "docs" / "04_connection_failed.png"),
            SERVER_PROFILE,
        )
        self.assertEqual(result.state, "connection_failed")

    def test_joining_failed_profile_requires_ok_anchor(self):
        without_ok = Recognition(
            "joining_failed",
            "JOINING FAILED",
            False,
            ("JOINING FAILED",),
        )
        with_ok = Recognition(
            "joining_failed",
            "JOINING FAILED Unknown Error OK",
            False,
            ("JOINING FAILED", "Unknown Error", "OK"),
            {"dismiss_joining_failed": (0.5, 0.63)},
        )
        self.assertFalse(
            self.reader._profile_result_is_credible(
                without_ok, JOINING_FAILED_PROFILE
            )
        )
        self.assertTrue(
            self.reader._profile_result_is_credible(
                with_ok, JOINING_FAILED_PROFILE
            )
        )

    def test_incomplete_profile_does_not_suppress_full_fallback(self):
        incomplete = OCRProfile(
            "incomplete_server",
            (arklogin.SERVER_HEADER, arklogin.SERVER_JOIN),
            frozenset({"server_list"}),
        )
        result, matched, attempts = self.reader.recognize_chain(
            reference_frame(ROOT / "docs" / "02_join-server.png"),
            (incomplete,),
            include_full_fallback=True,
        )
        self.assertEqual(result.state, "server_list")
        self.assertTrue(result.target_server_found)
        self.assertIsNone(matched)
        self.assertEqual(attempts, 2)

    def test_back_profile_requires_back_anchor_while_connecting(self):
        incomplete = OCRProfile(
            "back",
            (arklogin.SERVER_HEADER, arklogin.CONNECTING_TEXT),
            frozenset({"connecting"}),
        )
        result, matched, attempts = self.reader.recognize_chain(
            reference_frame(ROOT / "docs" / "05_cancel_after_connection_failed.png"),
            (incomplete,),
            include_full_fallback=True,
        )
        self.assertEqual(result.state, "connecting")
        self.assertIn("back", result.anchors)
        self.assertIsNone(matched)
        self.assertEqual(attempts, 2)


class AdaptiveOCRTests(unittest.TestCase):
    class FakeReader:
        def __init__(self):
            self.full_flags = []

        @staticmethod
        def visual_signature_chain(_frame, _profiles):
            return np.zeros((36, 64), dtype=np.uint8)

        def recognize_chain(self, _frame, _profiles, include_full_fallback):
            self.full_flags.append(include_full_fallback)
            attempts = 2 if include_full_fallback else 1
            return Recognition("unknown", "", False, ()), None, attempts

    def test_unknown_full_fallback_is_throttled(self):
        clock = FakeClock(1.0)
        bot = make_bot(clock)
        reader = self.FakeReader()
        bot.screen_reader = reader
        bot.active_profiles = (OUTCOME_PROFILE,)
        bot.last_state = "unknown"
        bot.last_frame_size = (20, 10)
        bot.last_full_scan_time = 0.0
        bot.profile_misses = 1
        bot.config["unchanged_ocr_refresh_seconds"] = 0.0
        bot.config["full_scan_fallback_seconds"] = 5.0
        frame = np.zeros((10, 20, 3), dtype=np.uint8)

        bot.recognize_adaptive(frame)
        clock.advance(4.0)
        bot.recognize_adaptive(frame)

        self.assertEqual(reader.full_flags, [False, True])
        self.assertEqual(bot.last_full_scan_time, 5.0)

    def test_startup_full_scan_sets_fallback_timestamp(self):
        clock = FakeClock(7.0)
        bot = make_bot(clock)
        reader = self.FakeReader()
        bot.screen_reader = reader
        bot.config["unchanged_ocr_refresh_seconds"] = 0.0

        bot.recognize_adaptive(np.zeros((10, 20, 3), dtype=np.uint8))

        self.assertEqual(reader.full_flags, [True])
        self.assertEqual(bot.last_full_scan_time, 7.0)

    def test_profile_miss_bypasses_cache_for_immediate_full_fallback(self):
        clock = FakeClock(10.0)
        bot = make_bot(clock)
        reader = self.FakeReader()
        bot.screen_reader = reader
        bot.active_profiles = (OUTCOME_PROFILE,)
        bot.last_state = "connecting"
        bot.last_frame_size = (20, 10)
        bot.config["unchanged_ocr_refresh_seconds"] = 10.0
        frame = np.zeros((10, 20, 3), dtype=np.uint8)

        bot.recognize_adaptive(frame)
        bot.recognize_adaptive(frame)

        self.assertEqual(reader.full_flags, [False, True])


if __name__ == "__main__":
    unittest.main()
