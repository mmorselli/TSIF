from __future__ import annotations

import argparse
import ctypes
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import cv2
import mss
import numpy as np
import psutil
import win32api
import win32con
import win32gui
import win32process
from rapidocr_onnxruntime import RapidOCR


APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "arklogin.log"
REFERENCE_CLIENT_WIDTH = 1920
REFERENCE_CLIENT_HEIGHT = 1152
OCR_MIN_WIDTH = 960
OCR_MAX_WIDTH = 1280
MASK_CACHE_LIMIT = 12


def canonical(value: str) -> str:
    """Return OCR text in a form suitable for tolerant comparisons."""
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def contains_any(text: str, phrases: Iterable[str]) -> bool:
    normalized = canonical(text)
    return any(canonical(phrase) in normalized for phrase in phrases)


def relative_click_point(
    bounds: tuple[int, int, int, int], position: list[float]
) -> tuple[int, int]:
    """Map reference coordinates to the visible ARK UI viewport.

    ARK keeps the 1920x1152 UI anchored to the top when the client is
    1920x1200, leaving extra space below it. Using the complete client height
    would therefore place bottom buttons about 40-50 pixels too low.
    """
    left, top, width, height = bounds
    ui_height = min(
        height, round(width * REFERENCE_CLIENT_HEIGHT / REFERENCE_CLIENT_WIDTH)
    )
    relative_x, relative_y = position
    return left + round(width * relative_x), top + round(ui_height * relative_y)


def set_dpi_awareness() -> None:
    """Make Win32 coordinates match physical pixels on scaled displays."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def configure_logging(verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("arklogin")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def load_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except FileNotFoundError as exc:
        raise ValueError(f"File di configurazione non trovato: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"JSON non valido in {path}, riga {exc.lineno}: {exc.msg}"
        ) from exc

    required = {
        "server_number",
        "event_screen_enabled",
        "window_title_contains",
        "process_name",
        "post_cancel_wait_seconds",
        "ocr_min_confidence",
        "click_positions",
    }
    missing = sorted(required.difference(config))
    if missing:
        raise ValueError(f"Parametri mancanti in config.json: {', '.join(missing)}")

    config["server_number"] = str(config["server_number"]).strip()
    if not config["server_number"].isdigit():
        raise ValueError("server_number deve contenere soltanto cifre")
    if not isinstance(config["event_screen_enabled"], bool):
        raise ValueError("event_screen_enabled deve essere true oppure false")

    # Backwards-compatible defaults for configuration files from the first
    # release. The active OCR loop and foreground reacquisition are deliberately
    # separate: fast UI reactions no longer make Ctrl+C difficult to reach.
    config.setdefault(
        "active_poll_interval_seconds",
        min(float(config.get("poll_interval_seconds", 0.25)), 0.25),
    )
    config.setdefault("foreground_reacquire_interval_seconds", 5.0)
    config.setdefault(
        "same_action_retry_seconds",
        min(float(config.get("action_cooldown_seconds", 0.75)), 0.75),
    )
    config.setdefault("recovery_timeout_seconds", 12.0)
    config.setdefault("success_unknown_confirm_seconds", 2.0)
    config.setdefault("success_pause_seconds", 60.0)
    config.setdefault("success_passive_poll_seconds", 2.0)
    config.setdefault("unchanged_ocr_refresh_seconds", 1.0)
    config.setdefault("visual_change_threshold", 3.0)
    config.setdefault("full_scan_fallback_seconds", 5.0)
    config.setdefault("click_hover_seconds", 0.05)
    config.setdefault("click_hold_seconds", 0.05)
    config.setdefault("click_restore_delay_seconds", 0.02)

    for key in (
        "active_poll_interval_seconds",
        "foreground_reacquire_interval_seconds",
        "same_action_retry_seconds",
        "post_cancel_wait_seconds",
        "recovery_timeout_seconds",
        "success_unknown_confirm_seconds",
        "success_pause_seconds",
        "success_passive_poll_seconds",
        "unchanged_ocr_refresh_seconds",
        "visual_change_threshold",
        "full_scan_fallback_seconds",
        "click_hover_seconds",
        "click_hold_seconds",
        "click_restore_delay_seconds",
    ):
        if not isinstance(config[key], (int, float)) or config[key] < 0:
            raise ValueError(f"{key} deve essere un numero maggiore o uguale a zero")

    if not 0 <= float(config["ocr_min_confidence"]) <= 1:
        raise ValueError("ocr_min_confidence deve essere compreso tra 0 e 1")

    expected_positions = {
        "start",
        "join_game",
        "join_server",
        "join_event",
        "cancel",
        "back",
        "accept_network_failure",
    }
    positions = config["click_positions"]
    if not isinstance(positions, dict) or not expected_positions.issubset(positions):
        raise ValueError(
            "click_positions deve contenere: " + ", ".join(sorted(expected_positions))
        )
    for name, position in positions.items():
        if (
            not isinstance(position, list)
            or len(position) != 2
            or not all(isinstance(value, (int, float)) for value in position)
            or not all(0 <= value <= 1 for value in position)
        ):
            raise ValueError(
                f"click_positions.{name} deve essere una coppia [x, y] tra 0 e 1"
            )
    return config


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    pid: int
    title: str


@dataclass(frozen=True)
class Recognition:
    state: str
    text: str
    target_server_found: bool
    lines: tuple[str, ...]
    anchors: dict[str, tuple[float, float]] = field(default_factory=dict)
    first_server_row_observed: bool = False


@dataclass(frozen=True)
class OCRDetection:
    text: str
    confidence: float
    center: tuple[float, float]


@dataclass(frozen=True)
class NormalizedRect:
    left: float
    top: float
    right: float
    bottom: float


@dataclass(frozen=True)
class OCRProfile:
    name: str
    zones: tuple[NormalizedRect, ...]
    allowed_states: frozenset[str]


@dataclass
class BackRecovery:
    phase: str
    started_at: float
    cancel_sent_at: float
    back_sent_at: float | None = None


@dataclass
class AttemptTracker:
    active: bool = False
    saw_connecting: bool = False
    unknown_since: float | None = None
    unknown_samples: int = 0
    pause_until: float = 0.0

    def reset(self) -> None:
        self.active = False
        self.saw_connecting = False
        self.unknown_since = None
        self.unknown_samples = 0


class ClickResult(str, Enum):
    SENT = "sent"
    DEBOUNCED = "debounced"
    FOCUS_LOST = "focus_lost"
    STALE = "stale"


SERVER_HEADER = NormalizedRect(0.035, 0.12, 0.30, 0.22)
SERVER_SELECTED = NormalizedRect(0.035, 0.285, 0.40, 0.375)
EVENT_HEADER = NormalizedRect(0.04, 0.12, 0.58, 0.29)
POPUP_TITLE = NormalizedRect(0.40, 0.33, 0.60, 0.41)
CONNECTING_TEXT = NormalizedRect(0.25, 0.38, 0.75, 0.59)
HOME_JOIN = NormalizedRect(0.10, 0.61, 0.40, 0.79)
POPUP_ACTIONS = NormalizedRect(0.39, 0.695, 0.61, 0.73)
START_ACTIONS = NormalizedRect(0.32, 0.68, 0.68, 0.95)
BACK_ACTION = NormalizedRect(0.02, 0.73, 0.20, 0.96)
EVENT_ACTIONS = NormalizedRect(0.04, 0.78, 0.37, 0.99)
SERVER_JOIN = NormalizedRect(0.79, 0.76, 0.99, 0.97)

MODAL_GUARD = (POPUP_TITLE, POPUP_ACTIONS)
START_PROFILE = OCRProfile(
    "start",
    MODAL_GUARD + (START_ACTIONS,),
    frozenset({"start", "network_failure", "connection_failed"}),
)
HOME_PROFILE = OCRProfile(
    "home",
    MODAL_GUARD + (HOME_JOIN,),
    frozenset({"home", "network_failure", "connection_failed"}),
)
SERVER_PROFILE = OCRProfile(
    "server",
    MODAL_GUARD + (SERVER_HEADER, SERVER_SELECTED, SERVER_JOIN),
    frozenset({"server_list", "network_failure", "connection_failed"}),
)
EVENT_PROFILE = OCRProfile(
    "event",
    MODAL_GUARD + (EVENT_HEADER, EVENT_ACTIONS),
    frozenset({"event", "network_failure", "connection_failed"}),
)
OUTCOME_PROFILE = OCRProfile(
    "outcome",
    MODAL_GUARD
    + (SERVER_HEADER, CONNECTING_TEXT, BACK_ACTION, START_ACTIONS),
    frozenset(
        {
            "connecting",
            "start",
            "network_failure",
            "connection_failed",
        }
    ),
)
BACK_PROFILE = OCRProfile(
    "back",
    MODAL_GUARD + (SERVER_HEADER, CONNECTING_TEXT, BACK_ACTION),
    frozenset(
        {
            "connecting",
            "server_list",
            "network_failure",
            "connection_failed",
        }
    ),
)

PROFILES_BY_STATE: dict[str, tuple[OCRProfile, ...]] = {
    "start": (START_PROFILE, HOME_PROFILE),
    "home": (HOME_PROFILE, SERVER_PROFILE),
    "server_list": (SERVER_PROFILE, EVENT_PROFILE, OUTCOME_PROFILE),
    "event": (EVENT_PROFILE, OUTCOME_PROFILE),
    "connecting": (OUTCOME_PROFILE,),
    "connection_failed": (BACK_PROFILE,),
    "network_failure": (START_PROFILE,),
    "unknown": (),
}

PROFILES_BY_ACTION: dict[str, tuple[OCRProfile, ...]] = {
    "accept_network_failure": (START_PROFILE, HOME_PROFILE),
    "start": (HOME_PROFILE, START_PROFILE),
    "join_game": (SERVER_PROFILE, HOME_PROFILE),
    "join_server": (EVENT_PROFILE, OUTCOME_PROFILE, SERVER_PROFILE),
    "join_event": (OUTCOME_PROFILE, EVENT_PROFILE),
    "cancel": (BACK_PROFILE,),
    "back": (HOME_PROFILE, BACK_PROFILE),
}


class WindowManager:
    def __init__(self, title_fragment: str, process_name: str, logger: logging.Logger):
        self.title_fragment = title_fragment.casefold()
        self.process_name = process_name.casefold().removesuffix(".exe")
        self.logger = logger
        self.cached_window: GameWindow | None = None

    def _cached_is_valid(self) -> bool:
        window = self.cached_window
        if window is None or not win32gui.IsWindow(window.hwnd):
            return False
        if not win32gui.IsWindowVisible(window.hwnd):
            return False
        title = win32gui.GetWindowText(window.hwnd).strip()
        return self.title_fragment in title.casefold()

    def find(self) -> GameWindow | None:
        if self._cached_is_valid():
            return self.cached_window

        candidates: list[GameWindow] = []

        def visit(hwnd: int, _: object) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd).strip()
            if self.title_fragment not in title.casefold():
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                actual_name = psutil.Process(pid).name().casefold().removesuffix(".exe")
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                return True
            if actual_name == self.process_name:
                candidates.append(GameWindow(hwnd=hwnd, pid=pid, title=title))
            return True

        win32gui.EnumWindows(visit, None)
        self.cached_window = candidates[0] if candidates else None
        return self.cached_window

    @staticmethod
    def client_bounds(window: GameWindow) -> tuple[int, int, int, int] | None:
        if win32gui.IsIconic(window.hwnd):
            return None
        left, top = win32gui.ClientToScreen(window.hwnd, (0, 0))
        client_left, client_top, client_right, client_bottom = win32gui.GetClientRect(
            window.hwnd
        )
        width = client_right - client_left
        height = client_bottom - client_top
        if width < 640 or height < 360:
            return None
        return left, top, width, height

    @staticmethod
    def activate(window: GameWindow) -> bool:
        hwnd = window.hwnd
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        foreground = win32gui.GetForegroundWindow()
        current_thread = win32api.GetCurrentThreadId()
        foreground_thread = (
            win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
        )
        attached = False
        try:
            if foreground_thread and foreground_thread != current_thread:
                attached = bool(
                    ctypes.windll.user32.AttachThreadInput(
                        current_thread, foreground_thread, True
                    )
                )
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except win32gui.error:
            return False
        finally:
            if attached:
                ctypes.windll.user32.AttachThreadInput(
                    current_thread, foreground_thread, False
                )
        time.sleep(0.15)
        return win32gui.GetForegroundWindow() == hwnd


class ScreenReader:
    def __init__(self, minimum_confidence: float, server_number: str):
        self.minimum_confidence = minimum_confidence
        self.server_number = canonical(server_number)
        # ARK renders horizontal text only. Skipping orientation classification
        # avoids processing every server-table cell a second time.
        self.ocr = RapidOCR(
            use_cls=False,
            det_limit_type="max",
            det_limit_side_len=OCR_MAX_WIDTH,
        )
        self.mask_cache: dict[tuple[str, int, int], np.ndarray] = {}

    @staticmethod
    def _prepare(frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        target_width = min(OCR_MAX_WIDTH, max(OCR_MIN_WIDTH, width))
        if width != target_width:
            scale = target_width / width
            frame = cv2.resize(
                frame,
                (target_width, max(1, round(height * scale))),
                interpolation=(
                    cv2.INTER_AREA if target_width < width else cv2.INTER_CUBIC
                ),
            )
        return frame

    @staticmethod
    def _ui_height(width: int, height: int) -> int:
        return min(
            height,
            round(width * REFERENCE_CLIENT_HEIGHT / REFERENCE_CLIENT_WIDTH),
        )

    def _profile_mask(
        self, profile: OCRProfile, width: int, height: int
    ) -> np.ndarray:
        key = (profile.name, width, height)
        cached = self.mask_cache.pop(key, None)
        if cached is not None:
            self.mask_cache[key] = cached
            return cached

        mask = np.zeros((height, width), dtype=np.uint8)
        ui_height = self._ui_height(width, height)
        padding = 0.015
        for zone in profile.zones:
            left = max(0.0, zone.left - padding)
            top = max(0.0, zone.top - padding)
            right = min(1.0, zone.right + padding)
            bottom = min(1.0, zone.bottom + padding)
            x1 = max(0, min(width, round(left * width)))
            x2 = max(0, min(width, round(right * width)))
            y1 = max(0, min(height, round(top * ui_height)))
            y2 = max(0, min(height, round(bottom * ui_height)))
            if x2 > x1 and y2 > y1:
                mask[y1:y2, x1:x2] = 255
        while len(self.mask_cache) >= MASK_CACHE_LIMIT:
            self.mask_cache.pop(next(iter(self.mask_cache)))
        self.mask_cache[key] = mask
        return mask

    def prepare(
        self, frame: np.ndarray, profile: OCRProfile | None = None
    ) -> np.ndarray:
        prepared = self._prepare(frame)
        if profile is None:
            return prepared
        height, width = prepared.shape[:2]
        mask = self._profile_mask(profile, width, height)
        return cv2.bitwise_and(prepared, prepared, mask=mask)

    def _read_prepared_detections(
        self, prepared: np.ndarray
    ) -> tuple[OCRDetection, ...]:
        height, width = prepared.shape[:2]
        result, _ = self.ocr(prepared, use_cls=False)
        if not result:
            return ()

        detections: list[OCRDetection] = []
        for item in result:
            if len(item) < 3:
                continue
            value = str(item[1]).strip()
            confidence = float(item[2])
            if not value or confidence < self.minimum_confidence:
                continue
            box = np.asarray(item[0], dtype=np.float64)
            if box.ndim != 2 or box.shape[1] < 2:
                continue
            center_x = float(np.mean(box[:, 0])) / width
            center_y = float(np.mean(box[:, 1])) / height
            detections.append(
                OCRDetection(
                    text=value,
                    confidence=confidence,
                    center=(
                        min(1.0, max(0.0, center_x)),
                        min(1.0, max(0.0, center_y)),
                    ),
                )
            )
        return tuple(detections)

    def read_detections(
        self, frame: np.ndarray, profile: OCRProfile | None = None
    ) -> tuple[OCRDetection, ...]:
        return self._read_prepared_detections(self.prepare(frame, profile))

    def read_lines(
        self, frame: np.ndarray, profile: OCRProfile | None = None
    ) -> tuple[str, ...]:
        return tuple(item.text for item in self.read_detections(frame, profile))

    def visual_signature(
        self, frame: np.ndarray, profile: OCRProfile | None
    ) -> np.ndarray:
        prepared = self.prepare(frame, profile)
        gray = cv2.cvtColor(prepared, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA)

    def visual_signature_chain(
        self, frame: np.ndarray, profiles: Sequence[OCRProfile]
    ) -> np.ndarray:
        prepared = self._prepare(frame)
        if profiles:
            height, width = prepared.shape[:2]
            combined_mask = np.zeros((height, width), dtype=np.uint8)
            for profile in profiles:
                combined_mask = cv2.bitwise_or(
                    combined_mask, self._profile_mask(profile, width, height)
                )
            prepared = cv2.bitwise_and(
                prepared, prepared, mask=combined_mask
            )
        gray = cv2.cvtColor(prepared, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA)

    @staticmethod
    def action_anchors(
        detections: Iterable[OCRDetection],
    ) -> dict[str, tuple[float, float]]:
        items = tuple(detections)

        def candidates(predicate: Any) -> list[OCRDetection]:
            return [item for item in items if predicate(canonical(item.text))]

        anchors: dict[str, tuple[float, float]] = {}

        join_game = candidates(lambda value: value == "JOINGAME")
        if join_game:
            anchors["join_game"] = max(
                join_game, key=lambda item: item.confidence
            ).center

        joins = candidates(lambda value: value == "JOIN")
        if joins:
            join = max(joins, key=lambda item: (item.center[1], item.confidence))
            anchors["join_server"] = join.center
            anchors["join_event"] = join.center

        cancel = candidates(lambda value: value == "CANCEL")
        if cancel:
            anchors["cancel"] = max(cancel, key=lambda item: item.confidence).center

        back = candidates(
            lambda value: value.endswith("BACK") and len(value) <= len("BBACK")
        )
        if back:
            anchors["back"] = max(
                back, key=lambda item: (item.center[1], item.confidence)
            ).center

        accept = candidates(lambda value: value == "ACCEPT")
        if accept:
            anchors["accept_network_failure"] = max(
                accept, key=lambda item: item.confidence
            ).center

        start = candidates(
            lambda value: "TOSTART" in value and "JOINLASTSESSION" not in value
        )
        if start:
            anchors["start"] = max(start, key=lambda item: item.confidence).center

        return anchors

    def classify_text(self, lines: Iterable[str]) -> Recognition:
        clean_lines = tuple(line.strip() for line in lines if line.strip())
        combined = " ".join(clean_lines)
        compact = canonical(combined)
        target_found = self._contains_server_number(compact)

        failed = (
            contains_any(combined, ("CONNECTION FAILED",))
            or contains_any(combined, ("SERVER IS FULL",))
            and contains_any(combined, ("CANCEL",))
        )
        event = contains_any(
            combined, ("REQUIRED MODS", "MODS TO DOWNLOAD", "TOTAL MODS ON SERVER")
        )
        network_failure = contains_any(
            combined, ("NETWORK FAILURE",)
        ) and contains_any(combined, ("ACCEPT",))
        start = (
            contains_any(combined, ("JOIN LAST SESSION",))
            and contains_any(combined, ("PRESS",))
            and contains_any(combined, ("TO START",))
        )
        home = contains_any(combined, ("JOIN GAME",))
        server_browser = contains_any(
            combined, ("MULTIPLAYER SERVERS", "SESSION NAME")
        )
        connecting = contains_any(combined, ("JOINING SERVER",))

        if failed:
            state = "connection_failed"
        elif network_failure:
            state = "network_failure"
        elif event:
            state = "event"
        elif home:
            state = "home"
        elif start:
            state = "start"
        elif server_browser and connecting:
            state = "connecting"
        elif server_browser:
            state = "server_list"
        else:
            state = "unknown"

        return Recognition(
            state=state,
            text=combined,
            target_server_found=target_found,
            lines=clean_lines,
        )

    def _contains_server_number(self, value: str) -> bool:
        """Match the configured id without accepting a longer numeric id."""
        return (
            re.search(
                rf"(?<!\d){re.escape(self.server_number)}(?!\d)",
                canonical(value),
            )
            is not None
        )

    def _server_is_first(
        self,
        detections: Iterable[OCRDetection],
        width: int,
        height: int,
    ) -> bool:
        return any(
            self._detection_is_in_first_server_row(item, width, height)
            and self._contains_server_number(item.text)
            for item in detections
        )

    def _detection_is_in_first_server_row(
        self,
        item: OCRDetection,
        width: int,
        height: int,
    ) -> bool:
        ui_height = self._ui_height(width, height)
        x = item.center[0]
        y = item.center[1] * height / max(1, ui_height)
        return (
            SERVER_SELECTED.left <= x <= SERVER_SELECTED.right
            and SERVER_SELECTED.top <= y <= SERVER_SELECTED.bottom
        )

    def _recognize_prepared(self, prepared: np.ndarray) -> Recognition:
        detections = self._read_prepared_detections(prepared)
        result = self.classify_text(item.text for item in detections)
        target_found = result.target_server_found
        first_row_observed = False
        if result.state == "server_list":
            height, width = prepared.shape[:2]
            first_row_observed = any(
                self._detection_is_in_first_server_row(item, width, height)
                for item in detections
            )
            target_found = self._server_is_first(detections, width, height)
        return Recognition(
            state=result.state,
            text=result.text,
            target_server_found=target_found,
            lines=result.lines,
            anchors=self.action_anchors(detections),
            first_server_row_observed=first_row_observed,
        )

    def recognize(
        self, frame: np.ndarray, profile: OCRProfile | None = None
    ) -> Recognition:
        return self._recognize_prepared(self.prepare(frame, profile))

    def recognize_chain(
        self,
        frame: np.ndarray,
        profiles: Sequence[OCRProfile],
        include_full_fallback: bool,
    ) -> tuple[Recognition, OCRProfile | None, int]:
        prepared = self._prepare(frame)
        height, width = prepared.shape[:2]
        attempts = 0
        for profile in profiles:
            mask = self._profile_mask(profile, width, height)
            masked = cv2.bitwise_and(prepared, prepared, mask=mask)
            result = self._recognize_prepared(masked)
            attempts += 1
            if (
                result.state in profile.allowed_states
                and self._profile_result_is_credible(result, profile)
            ):
                return result, profile, attempts

        if include_full_fallback or not profiles:
            attempts += 1
            return self._recognize_prepared(prepared), None, attempts
        return Recognition("unknown", "", False, ()), None, attempts

    @staticmethod
    def _profile_result_is_credible(
        result: Recognition, profile: OCRProfile
    ) -> bool:
        """Require key evidence before trusting OCR from a partial canvas."""
        required_anchor = {
            "start": "start",
            "home": "join_game",
            "server_list": "join_server",
            "event": "join_event",
            "connection_failed": "cancel",
            "network_failure": "accept_network_failure",
        }.get(result.state)
        if required_anchor is not None and required_anchor not in result.anchors:
            return False
        if result.state == "server_list" and not (
            result.target_server_found or result.first_server_row_observed
        ):
            return False
        if result.state == "event" and not result.target_server_found:
            return False
        if (
            profile.name == BACK_PROFILE.name
            and result.state in {"server_list", "connecting"}
            and "back" not in result.anchors
        ):
            return False
        return True


class ArkLoginBot:
    def __init__(
        self,
        config: dict[str, Any],
        logger: logging.Logger,
        dry_run: bool = False,
        max_actions: int | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ):
        self.config = config
        self.logger = logger
        self.dry_run = dry_run
        self.max_actions = max_actions
        self.now = clock or time.monotonic
        self.sleep = sleeper or time.sleep
        self.actions = 0
        self.back_recovery: BackRecovery | None = None
        self.last_sent_by_action: dict[str, float] = {}
        self.last_state: str | None = None
        self.last_notice: tuple[str, float] = ("", 0.0)
        self.active_profiles: tuple[OCRProfile, ...] = ()
        self.profile_misses = 0
        self.last_full_scan_time = float("-inf")
        self.last_frame_size: tuple[int, int] | None = None
        self.cached_signature: np.ndarray | None = None
        self.cached_profile_key: tuple[str, ...] | None = None
        self.cached_recognition: Recognition | None = None
        self.cached_ocr_time = float("-inf")
        self.next_scan_at = 0.0
        self.background_since: float | None = None
        self.next_focus_attempt_at: float | None = None
        self.attempt = AttemptTracker()
        self.window_manager = WindowManager(
            config["window_title_contains"], config["process_name"], logger
        )
        self.screen_reader = ScreenReader(
            float(config["ocr_min_confidence"]), config["server_number"]
        )
        self.capture = mss.mss()

    def notice(self, key: str, message: str, interval: float = 10.0) -> None:
        now = self.now()
        last_key, last_time = self.last_notice
        if key != last_key or now - last_time >= interval:
            self.logger.info(message)
            self.last_notice = (key, now)

    def grab(self, window: GameWindow) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
        bounds = self.window_manager.client_bounds(window)
        if bounds is None:
            return None
        left, top, width, height = bounds
        shot = self.capture.grab(
            {"left": left, "top": top, "width": width, "height": height}
        )
        frame = np.asarray(shot, dtype=np.uint8)[:, :, :3]
        return frame, bounds

    def invalidate_visual_cache(self) -> None:
        self.cached_signature = None
        self.cached_profile_key = None
        self.cached_recognition = None

    def set_profiles(self, profiles: Sequence[OCRProfile]) -> None:
        new_profiles = tuple(profiles)
        if tuple(item.name for item in new_profiles) != tuple(
            item.name for item in self.active_profiles
        ):
            self.active_profiles = new_profiles
            self.profile_misses = 0
            self.invalidate_visual_cache()

    def click(
        self,
        window: GameWindow,
        bounds: tuple[int, int, int, int],
        position_name: str,
        description: str,
        detected_position: tuple[float, float] | None = None,
    ) -> ClickResult:
        now = self.now()
        last_sent = self.last_sent_by_action.get(position_name, float("-inf"))
        if now - last_sent < float(self.config["same_action_retry_seconds"]):
            return ClickResult.DEBOUNCED

        if not self.dry_run and win32gui.GetForegroundWindow() != window.hwnd:
            self.logger.debug(
                "Clic %s annullato: ARK non è più in primo piano.", position_name
            )
            return ClickResult.FOCUS_LOST

        fresh_bounds = self.window_manager.client_bounds(window)
        if fresh_bounds is None or fresh_bounds != bounds:
            self.logger.debug(
                "Clic %s annullato: dimensioni o posizione della finestra cambiate.",
                position_name,
            )
            return ClickResult.STALE

        left, top, width, height = fresh_bounds
        if detected_position is not None:
            x = left + round(width * detected_position[0])
            y = top + round(height * detected_position[1])
            self.logger.debug(
                "Pulsante %s individuato dall'OCR al punto client (%d, %d).",
                position_name,
                x - left,
                y - top,
            )
        else:
            x, y = relative_click_point(
                bounds, self.config["click_positions"][position_name]
            )
            self.logger.debug(
                "Pulsante %s non localizzato dall'OCR: uso la posizione di fallback.",
                position_name,
            )

        if self.dry_run:
            self.logger.info("[SIMULAZIONE] %s", description)
        else:
            previous_cursor = win32api.GetCursorPos()
            button_down = False
            try:
                win32api.SetCursorPos((x, y))
                self.sleep(float(self.config["click_hover_seconds"]))
                if win32gui.GetForegroundWindow() != window.hwnd:
                    self.logger.debug(
                        "Clic %s annullato subito prima della pressione: ARK "
                        "non è più in primo piano.",
                        position_name,
                    )
                    return ClickResult.FOCUS_LOST
                if self.window_manager.client_bounds(window) != fresh_bounds:
                    self.logger.debug(
                        "Clic %s annullato subito prima della pressione: "
                        "dimensioni o posizione della finestra cambiate.",
                        position_name,
                    )
                    return ClickResult.STALE
                win32api.mouse_event(
                    win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0
                )
                button_down = True
                self.sleep(float(self.config["click_hold_seconds"]))
                win32api.mouse_event(
                    win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0
                )
                button_down = False
            finally:
                # Ctrl+C may arrive during either short click delay. Always
                # release the global mouse button and restore the pointer
                # before propagating the interruption.
                try:
                    if button_down:
                        win32api.mouse_event(
                            win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0
                        )
                finally:
                    if bool(self.config.get("restore_mouse_position", True)):
                        try:
                            self.sleep(
                                float(self.config["click_restore_delay_seconds"])
                            )
                        finally:
                            win32api.SetCursorPos(previous_cursor)
            self.logger.info("%s", description)

        self.last_sent_by_action[position_name] = self.now()
        self.actions += 1
        return ClickResult.SENT

    def perform_action(
        self,
        recognition: Recognition,
        window: GameWindow,
        bounds: tuple[int, int, int, int],
        position_name: str,
        description: str,
    ) -> ClickResult:
        result = self.click(
            window,
            bounds,
            position_name,
            description,
            recognition.anchors.get(position_name),
        )
        if result is ClickResult.SENT:
            self.set_profiles(PROFILES_BY_ACTION.get(position_name, ()))
            # A screen transition may immediately use one complete fallback
            # if its expected profile loses key evidence after a resize.
            self.last_full_scan_time = float("-inf")
            if position_name in {"join_server", "join_event"}:
                # A newly sent JOIN starts a distinct attempt. In particular,
                # do not carry a previous "connecting" observation into the
                # success heuristic for this click.
                self.attempt.reset()
                self.attempt.active = True
                self.attempt.pause_until = 0.0
        return result

    def _profile_key(self) -> tuple[str, ...]:
        return tuple(profile.name for profile in self.active_profiles)

    def recognize_adaptive(
        self, frame: np.ndarray
    ) -> tuple[Recognition, bool, int, float]:
        now = self.now()
        frame_size = (frame.shape[1], frame.shape[0])
        resized = self.last_frame_size is not None and frame_size != self.last_frame_size
        self.last_frame_size = frame_size
        if resized:
            self.profile_misses = 1
            self.invalidate_visual_cache()

        signature = self.screen_reader.visual_signature_chain(
            frame, self.active_profiles
        )
        profile_key = self._profile_key()
        full_fallback_due = (
            self.profile_misses >= 1
            and now - self.last_full_scan_time
            >= float(self.config["full_scan_fallback_seconds"])
        )
        if (
            not resized
            and not full_fallback_due
            and self.cached_signature is not None
            and self.cached_profile_key == profile_key
            and self.cached_recognition is not None
            and now - self.cached_ocr_time
            < float(self.config["unchanged_ocr_refresh_seconds"])
        ):
            difference = float(
                np.mean(
                    cv2.absdiff(signature, self.cached_signature),
                    dtype=np.float64,
                )
            )
            if difference <= float(self.config["visual_change_threshold"]):
                return self.cached_recognition, True, 0, 0.0

        include_full = (
            resized
            or not self.active_profiles
            or full_fallback_due
            or (
                self.last_state in {None, "unknown"}
                and now - self.last_full_scan_time
                >= float(self.config["full_scan_fallback_seconds"])
            )
        )
        started = self.now()
        recognition, matched_profile, attempts = self.screen_reader.recognize_chain(
            frame,
            self.active_profiles,
            include_full_fallback=include_full,
        )
        elapsed = self.now() - started

        if matched_profile is None:
            if include_full:
                # `recognize_chain` reaches the full canvas only when no
                # profile matched. Remember that scan even at startup, so an
                # unchanged unknown/game screen cannot trigger a full OCR on
                # every second profile miss.
                self.last_full_scan_time = now
                self.profile_misses = 0
            elif self.active_profiles and recognition.state == "unknown":
                self.profile_misses += 1
        else:
            self.profile_misses = 0

        self.cached_signature = signature
        self.cached_profile_key = profile_key
        self.cached_recognition = recognition
        self.cached_ocr_time = now
        if recognition.state != "unknown":
            self.set_profiles(PROFILES_BY_STATE.get(recognition.state, ()))
        return recognition, False, attempts, elapsed

    def track_attempt(self, recognition: Recognition, fresh: bool = True) -> None:
        now = self.now()
        state = recognition.state
        if state == "connecting":
            if self.attempt.active:
                self.attempt.saw_connecting = True
            self.attempt.unknown_since = None
            self.attempt.unknown_samples = 0
            return

        if state == "unknown" and self.attempt.active and self.attempt.saw_connecting:
            # Replaying a cached OCR result proves only that the pixels are
            # unchanged; it is not an independent observation for declaring
            # the login successful.
            if not fresh:
                return
            if self.attempt.unknown_since is None:
                self.attempt.unknown_since = now
                self.attempt.unknown_samples = 1
                return
            self.attempt.unknown_samples += 1
            if (
                self.attempt.unknown_samples >= 2
                and now - self.attempt.unknown_since
                >= float(self.config["success_unknown_confirm_seconds"])
            ):
                self.attempt.pause_until = now + float(
                    self.config["success_pause_seconds"]
                )
                self.attempt.reset()
                self.set_profiles((OUTCOME_PROFILE,))
                self.logger.info(
                    "Probabile connessione riuscita: automazione in pausa per %.0f "
                    "secondi. Puoi premere Ctrl+C senza che ARK riprenda il focus.",
                    float(self.config["success_pause_seconds"]),
                )
            return

        if state in {
            "connection_failed",
            "network_failure",
            "home",
            "start",
        }:
            self.attempt.reset()
            self.attempt.pause_until = 0.0
        elif state in {"server_list", "event"} and self.attempt.saw_connecting:
            # Returning to an interactive login screen after CONNECTING is a
            # failed/aborted attempt, not a successful transition to gameplay.
            self.attempt.reset()
            self.attempt.pause_until = 0.0
        elif state != "unknown":
            self.attempt.unknown_since = None
            self.attempt.unknown_samples = 0

    def handle_back_recovery(
        self,
        recognition: Recognition,
        window: GameWindow,
        bounds: tuple[int, int, int, int],
    ) -> bool:
        recovery = self.back_recovery
        if recovery is None:
            return False
        now = self.now()

        if recognition.state in {"home", "start", "network_failure"}:
            self.back_recovery = None
            return False

        if now - recovery.started_at >= float(
            self.config["recovery_timeout_seconds"]
        ):
            if recognition.state in {"server_list", "connecting"}:
                self.logger.warning(
                    "Recupero CANCEL/BACK ancora bloccato: riprovo BACK."
                )
                result = self.perform_action(
                    recognition,
                    window,
                    bounds,
                    "back",
                    "Recupero ancora bloccato: riprovo BACK.",
                )
                recovery.started_at = now
                if result is ClickResult.SENT:
                    recovery.phase = "wait_back_exit"
                    recovery.back_sent_at = now
                return True
            if recognition.state == "connection_failed":
                self.logger.warning(
                    "CANCEL non è stato recepito: avvio un nuovo recupero."
                )
                self.back_recovery = None
                return False
            self.logger.warning(
                "Recupero CANCEL/BACK in attesa di una schermata nota."
            )
            recovery.started_at = now
            return True

        if recovery.phase == "wait_back_ready":
            if recognition.state == "connection_failed":
                return True
            if (
                recognition.state in {"server_list", "connecting"}
                and now - recovery.cancel_sent_at
                >= float(self.config["post_cancel_wait_seconds"])
            ):
                result = self.perform_action(
                    recognition,
                    window,
                    bounds,
                    "back",
                    "Tentativo annullato: premo BACK e preparo un nuovo ciclo.",
                )
                if result is ClickResult.SENT:
                    recovery.phase = "wait_back_exit"
                    recovery.back_sent_at = now
            return True

        if recovery.phase == "wait_back_exit":
            if recognition.state in {"server_list", "connecting"}:
                self.perform_action(
                    recognition,
                    window,
                    bounds,
                    "back",
                    "BACK non ancora recepito: riprovo.",
                )
            return True
        return True

    def handle(
        self,
        recognition: Recognition,
        window: GameWindow,
        bounds: tuple[int, int, int, int],
        recognition_fresh: bool = True,
    ) -> None:
        if recognition.state != self.last_state:
            self.logger.info("Schermata riconosciuta: %s", recognition.state)
            self.logger.debug("Testo OCR: %s", " | ".join(recognition.lines))
            self.last_state = recognition.state

        self.track_attempt(recognition, fresh=recognition_fresh)
        if self.handle_back_recovery(recognition, window, bounds):
            return

        if recognition.state == "connection_failed":
            result = self.perform_action(
                recognition,
                window,
                bounds,
                "cancel",
                "Server pieno: premo CANCEL.",
            )
            if result is ClickResult.SENT:
                now = self.now()
                self.back_recovery = BackRecovery(
                    phase="wait_back_ready",
                    started_at=now,
                    cancel_sent_at=now,
                )
            return

        if recognition.state == "network_failure":
            self.perform_action(
                recognition,
                window,
                bounds,
                "accept_network_failure",
                "Messaggio di rete/server pieno: premo ACCEPT.",
            )
            return

        if recognition.state == "home":
            self.perform_action(
                recognition,
                window,
                bounds,
                "join_game",
                "Schermata iniziale: premo JOIN GAME.",
            )
            return

        if recognition.state == "start":
            self.perform_action(
                recognition,
                window,
                bounds,
                "start",
                "Schermata di avvio: premo PRESS TO START.",
            )
            return

        if recognition.state == "server_list":
            if recognition.target_server_found:
                self.perform_action(
                    recognition,
                    window,
                    bounds,
                    "join_server",
                    f"Server {self.config['server_number']} verificato: premo JOIN.",
                )
            else:
                self.notice(
                    "server_missing",
                    f"Lista pronta, ma il server {self.config['server_number']} "
                    "non è visibile: nessun clic.",
                )
            return

        if recognition.state == "event":
            if not bool(self.config["event_screen_enabled"]):
                self.notice(
                    "event_disabled",
                    "Schermata evento/mod richiesta rilevata, ma la gestione è "
                    "disattivata in config.json.",
                )
            elif recognition.target_server_found:
                self.perform_action(
                    recognition,
                    window,
                    bounds,
                    "join_event",
                    f"Conferma evento/mod per il server "
                    f"{self.config['server_number']}: premo JOIN.",
                )
            else:
                self.notice(
                    "event_server_mismatch",
                    "Conferma evento/mod rilevata, ma il numero del server non "
                    "corrisponde: nessun clic.",
                )
            return

        if recognition.state == "connecting":
            self.notice(
                "connecting",
                "Connessione in corso: attendo l'esito senza interrompere il tentativo.",
                interval=20.0,
            )

    def step(self) -> float:
        now = self.now()
        window = self.window_manager.find()
        if window is None:
            self.notice(
                "window_missing",
                "Attendo la finestra di ARK: Survival Ascended...",
            )
            return 0.5

        foreground = win32gui.GetForegroundWindow() == window.hwnd
        pause_active = now < self.attempt.pause_until
        if not foreground:
            if pause_active:
                return 0.25
            if self.background_since is None:
                self.background_since = now
                self.next_focus_attempt_at = now + float(
                    self.config["foreground_reacquire_interval_seconds"]
                )
            if self.next_focus_attempt_at is None:
                self.next_focus_attempt_at = now + float(
                    self.config["foreground_reacquire_interval_seconds"]
                )
            if now < self.next_focus_attempt_at:
                return min(0.25, max(0.01, self.next_focus_attempt_at - now))

            self.next_focus_attempt_at = now + float(
                self.config["foreground_reacquire_interval_seconds"]
            )
            if not self.window_manager.activate(window):
                self.notice(
                    "focus_failed",
                    "ARK non può essere portato in primo piano: attendo.",
                    interval=5.0,
                )
                return 0.25
            self.background_since = None
        else:
            self.background_since = None
            self.next_focus_attempt_at = None

        if now < self.next_scan_at:
            return min(0.25, max(0.01, self.next_scan_at - now))

        started = self.now()
        captured = self.grab(window)
        if captured is None:
            self.notice(
                "window_unavailable",
                "La finestra di ARK è minimizzata o troppo piccola: attendo.",
            )
            self.next_scan_at = started + float(
                self.config["active_poll_interval_seconds"]
            )
            return 0.25

        frame, bounds = captured
        recognition, cache_hit, attempts, ocr_elapsed = self.recognize_adaptive(frame)
        self.logger.debug(
            "Scansione OCR: %.3f s, profili=%s, tentativi=%d, cache=%s.",
            ocr_elapsed,
            self._profile_key() or ("full",),
            attempts,
            cache_hit,
        )
        self.handle(
            recognition,
            window,
            bounds,
            recognition_fresh=not cache_hit,
        )

        interval = (
            float(self.config["success_passive_poll_seconds"])
            if self.now() < self.attempt.pause_until
            else float(self.config["active_poll_interval_seconds"])
        )
        self.next_scan_at = started + interval
        return max(0.05, self.next_scan_at - self.now())

    def run(self) -> None:
        self.logger.info(
            "ARK Login avviato per il server %s. Premi Ctrl+C per fermarlo.",
            self.config["server_number"],
        )
        if self.dry_run:
            self.logger.info("Modalità simulazione attiva: nessun clic verrà eseguito.")

        while self.max_actions is None or self.actions < self.max_actions:
            delay = self.step()
            self.sleep(min(0.25, max(0.01, delay)))

        self.logger.info("Numero massimo di azioni raggiunto; arresto completato.")


def reference_frame(path: Path) -> np.ndarray:
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError(f"Impossibile leggere l'immagine: {path}")
    height, width = frame.shape[:2]
    title_sample = frame[: max(1, round(height * 0.05)), width // 4 : width * 3 // 4]
    if float(np.mean(title_sample)) > 150:
        frame = frame[round(height * 0.04) :, :]
    return frame


def check_reference_images(
    config: dict[str, Any], logger: logging.Logger, docs_dir: Path
) -> int:
    expected = (
        ("00_start_screen.png", "start", START_PROFILE, "start", False),
        ("01_first_screen.png", "home", HOME_PROFILE, "join_game", False),
        (
            "02_join-server.png",
            "server_list",
            SERVER_PROFILE,
            "join_server",
            True,
        ),
        ("03_optional_event.png", "event", EVENT_PROFILE, "join_event", True),
        (
            "04_connection_failed.png",
            "connection_failed",
            OUTCOME_PROFILE,
            "cancel",
            False,
        ),
        (
            "05_cancel_after_connection_failed.png",
            "connecting",
            BACK_PROFILE,
            "back",
            True,
        ),
        (
            "06_server_full_press_accept.png",
            "network_failure",
            OUTCOME_PROFILE,
            "accept_network_failure",
            False,
        ),
    )
    reader = ScreenReader(
        float(config["ocr_min_confidence"]), config["server_number"]
    )
    failures = 0
    total_elapsed = 0.0
    for name, expected_state, profile, expected_anchor, expected_target in expected:
        path = docs_dir / name
        started = time.perf_counter()
        recognition = reader.recognize(reference_frame(path), profile)
        elapsed = time.perf_counter() - started
        total_elapsed += elapsed
        anchor_found = expected_anchor in recognition.anchors
        matches = (
            recognition.state == expected_state
            and anchor_found
            and recognition.target_server_found == expected_target
        )
        failures += 0 if matches else 1
        logger.info(
            "%s -> %-18s atteso=%-18s profilo=%-7s OCR=%.3fs "
            "server_%s=%s pulsante_%s=%s",
            name,
            recognition.state,
            expected_state,
            profile.name,
            elapsed,
            config["server_number"],
            recognition.target_server_found,
            expected_anchor,
            anchor_found,
        )
        logger.debug("Testo OCR %s: %s", name, " | ".join(recognition.lines))
    if failures:
        logger.error("Verifica immagini fallita per %d schermate.", failures)
        return 1
    logger.info(
        "Tutte le schermate di riferimento sono state riconosciute in %.3f s.",
        total_elapsed,
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automatizza i tentativi di accesso a un server ARK ASA pieno."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Percorso del file JSON di configurazione.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Riconosce le schermate e registra le azioni senza fare clic.",
    )
    parser.add_argument(
        "--check-images",
        action="store_true",
        help=(
            "Verifica e misura i profili OCR ottimizzati usando gli screenshot "
            "nella cartella docs."
        ),
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Mostra anche i dettagli diagnostici."
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=None,
        help="Si arresta dopo questo numero di azioni (utile per prove controllate).",
    )
    return parser.parse_args()


def main() -> int:
    set_dpi_awareness()
    args = parse_args()
    logger = configure_logging(args.verbose)
    try:
        config = load_config(args.config.resolve())
        if args.max_actions is not None and args.max_actions <= 0:
            raise ValueError("--max-actions deve essere maggiore di zero")
        if args.check_images:
            return check_reference_images(config, logger, APP_DIR / "docs")
        bot = ArkLoginBot(config, logger, args.dry_run, args.max_actions)
        bot.run()
        return 0
    except KeyboardInterrupt:
        logger.info("Arresto richiesto dall'utente.")
        return 0
    except (ValueError, OSError, psutil.Error) as exc:
        logger.error("%s", exc)
        return 2
    except Exception:
        logger.exception("Errore inatteso.")
        return 3


if __name__ == "__main__":
    sys.exit(main())
