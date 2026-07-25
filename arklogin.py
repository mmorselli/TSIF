from __future__ import annotations

import argparse
import ctypes
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterable

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
        "poll_interval_seconds",
        "action_cooldown_seconds",
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

    for key in (
        "poll_interval_seconds",
        "action_cooldown_seconds",
        "post_cancel_wait_seconds",
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


@dataclass(frozen=True)
class OCRDetection:
    text: str
    confidence: float
    center: tuple[float, float]


class WindowManager:
    def __init__(self, title_fragment: str, process_name: str, logger: logging.Logger):
        self.title_fragment = title_fragment.casefold()
        self.process_name = process_name.casefold().removesuffix(".exe")
        self.logger = logger

    def find(self) -> GameWindow | None:
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
        return candidates[0] if candidates else None

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
        self.ocr = RapidOCR()

    @staticmethod
    def _prepare(frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        maximum_width = 1600
        if width > maximum_width:
            scale = maximum_width / width
            frame = cv2.resize(
                frame,
                (maximum_width, max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
        return frame

    def read_detections(self, frame: np.ndarray) -> tuple[OCRDetection, ...]:
        prepared = self._prepare(frame)
        height, width = prepared.shape[:2]
        result, _ = self.ocr(prepared)
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

    def read_lines(self, frame: np.ndarray) -> tuple[str, ...]:
        return tuple(item.text for item in self.read_detections(frame))

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
        target_found = self.server_number in compact

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

    def recognize(self, frame: np.ndarray) -> Recognition:
        detections = self.read_detections(frame)
        result = self.classify_text(item.text for item in detections)
        return Recognition(
            state=result.state,
            text=result.text,
            target_server_found=result.target_server_found,
            lines=result.lines,
            anchors=self.action_anchors(detections),
        )


class ArkLoginBot:
    def __init__(
        self,
        config: dict[str, Any],
        logger: logging.Logger,
        dry_run: bool = False,
        max_actions: int | None = None,
    ):
        self.config = config
        self.logger = logger
        self.dry_run = dry_run
        self.max_actions = max_actions
        self.actions = 0
        self.pending_back = False
        self.cancel_time = 0.0
        self.last_action_time = 0.0
        self.last_state: str | None = None
        self.last_notice: tuple[str, float] = ("", 0.0)
        self.window_manager = WindowManager(
            config["window_title_contains"], config["process_name"], logger
        )
        self.screen_reader = ScreenReader(
            float(config["ocr_min_confidence"]), config["server_number"]
        )
        self.capture = mss.mss()

    def notice(self, key: str, message: str, interval: float = 10.0) -> None:
        now = time.monotonic()
        last_key, last_time = self.last_notice
        if key != last_key or now - last_time >= interval:
            self.logger.info(message)
            self.last_notice = (key, now)

    def grab(self, window: GameWindow) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
        bounds = self.window_manager.client_bounds(window)
        if bounds is None:
            return None
        if win32gui.GetForegroundWindow() != window.hwnd:
            if not self.window_manager.activate(window):
                self.logger.warning(
                    "Impossibile portare ARK in primo piano per acquisire "
                    "un'immagine pulita."
                )
                return None
            time.sleep(0.2)
        left, top, width, height = bounds
        shot = self.capture.grab(
            {"left": left, "top": top, "width": width, "height": height}
        )
        frame = np.asarray(shot, dtype=np.uint8)[:, :, :3]
        return frame, bounds

    def may_act(self) -> bool:
        return (
            time.monotonic() - self.last_action_time
            >= float(self.config["action_cooldown_seconds"])
        )

    def click(
        self,
        window: GameWindow,
        bounds: tuple[int, int, int, int],
        position_name: str,
        description: str,
        detected_position: tuple[float, float] | None = None,
    ) -> bool:
        if not self.may_act():
            return False
        left, top, width, height = bounds
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

        self.logger.info("%s%s", "[SIMULAZIONE] " if self.dry_run else "", description)
        if not self.dry_run:
            if not self.window_manager.activate(window):
                self.logger.warning(
                    "Impossibile portare ARK in primo piano: clic annullato per sicurezza."
                )
                return False
            previous_cursor = win32api.GetCursorPos()
            win32api.SetCursorPos((x, y))
            time.sleep(0.2)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.12)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            if bool(self.config.get("restore_mouse_position", True)):
                time.sleep(0.25)
                win32api.SetCursorPos(previous_cursor)

        self.last_action_time = time.monotonic()
        self.actions += 1
        return True

    def handle(
        self,
        recognition: Recognition,
        window: GameWindow,
        bounds: tuple[int, int, int, int],
    ) -> None:
        if recognition.state != self.last_state:
            self.logger.info("Schermata riconosciuta: %s", recognition.state)
            self.logger.debug("Testo OCR: %s", " | ".join(recognition.lines))
            self.last_state = recognition.state

        if recognition.state == "connection_failed":
            if self.click(
                window,
                bounds,
                "cancel",
                "Server pieno: premo CANCEL.",
                recognition.anchors.get("cancel"),
            ):
                self.pending_back = True
                self.cancel_time = time.monotonic()
            return

        if recognition.state == "network_failure":
            self.click(
                window,
                bounds,
                "accept_network_failure",
                "Messaggio di rete/server pieno: premo ACCEPT.",
                recognition.anchors.get("accept_network_failure"),
            )
            return

        if self.pending_back:
            wait_elapsed = (
                time.monotonic() - self.cancel_time
                >= float(self.config["post_cancel_wait_seconds"])
            )
            if wait_elapsed and recognition.state in {"server_list", "connecting"}:
                if self.click(
                    window,
                    bounds,
                    "back",
                    "Tentativo annullato: premo BACK e preparo un nuovo ciclo.",
                    recognition.anchors.get("back"),
                ):
                    self.pending_back = False
            return

        if recognition.state == "home":
            self.click(
                window,
                bounds,
                "join_game",
                "Schermata iniziale: premo JOIN GAME.",
                recognition.anchors.get("join_game"),
            )
            return

        if recognition.state == "start":
            self.click(
                window,
                bounds,
                "start",
                "Schermata di avvio: premo PRESS TO START.",
                recognition.anchors.get("start"),
            )
            return

        if recognition.state == "server_list":
            if recognition.target_server_found:
                self.click(
                    window,
                    bounds,
                    "join_server",
                    f"Server {self.config['server_number']} verificato: premo JOIN.",
                    recognition.anchors.get("join_server"),
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
                self.click(
                    window,
                    bounds,
                    "join_event",
                    f"Conferma evento/mod per il server "
                    f"{self.config['server_number']}: premo JOIN.",
                    recognition.anchors.get("join_event"),
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

    def run(self) -> None:
        self.logger.info(
            "ARK Login avviato per il server %s. Premi Ctrl+C per fermarlo.",
            self.config["server_number"],
        )
        if self.dry_run:
            self.logger.info("Modalità simulazione attiva: nessun clic verrà eseguito.")

        while self.max_actions is None or self.actions < self.max_actions:
            started = time.monotonic()
            window = self.window_manager.find()
            if window is None:
                self.notice(
                    "window_missing",
                    "Attendo la finestra di ARK: Survival Ascended...",
                )
            else:
                captured = self.grab(window)
                if captured is None:
                    self.notice(
                        "window_unavailable",
                        "La finestra di ARK è minimizzata o troppo piccola: attendo.",
                    )
                else:
                    frame, bounds = captured
                    recognition = self.screen_reader.recognize(frame)
                    self.handle(recognition, window, bounds)

            elapsed = time.monotonic() - started
            sleep_time = max(0.05, float(self.config["poll_interval_seconds"]) - elapsed)
            time.sleep(sleep_time)

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
    expected = {
        "00_start_screen.png": "start",
        "01_first_screen.png": "home",
        "02_join-server.png": "server_list",
        "03_optional_event.png": "event",
        "04_connection_failed.png": "connection_failed",
        "05_cancel_after_connection_failed.png": "connecting",
        "06_server_full_press_accept.png": "network_failure",
    }
    reader = ScreenReader(
        float(config["ocr_min_confidence"]), config["server_number"]
    )
    failures = 0
    for name, expected_state in expected.items():
        path = docs_dir / name
        recognition = reader.recognize(reference_frame(path))
        matches = recognition.state == expected_state
        failures += 0 if matches else 1
        logger.info(
            "%s -> %-18s atteso=%-18s server_%s=%s",
            name,
            recognition.state,
            expected_state,
            config["server_number"],
            recognition.target_server_found,
        )
        logger.debug("Testo OCR %s: %s", name, " | ".join(recognition.lines))
    if failures:
        logger.error("Verifica immagini fallita per %d schermate.", failures)
        return 1
    logger.info("Tutte le schermate di riferimento sono state riconosciute.")
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
        help="Verifica il riconoscimento usando gli screenshot nella cartella docs.",
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
