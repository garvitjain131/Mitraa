"""Entry point: python -m mitraa.assistant [--detect | --install-startup | --uninstall-startup]"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import tkinter as tk
from pathlib import Path

from mitraa.assistant.hotkey import HoldDetector, HotkeyListener, KeySpec, detect_keys, parse_hotkeys
from mitraa.config import BACKEND_DIR, get_settings

logger = logging.getLogger("mitraa.assistant")

LOCK_PORT = 47811  # single-instance guard (localhost only)
STARTUP_FILE = "Mitraa Assistant.vbs"
_FRIENDLY = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "fn": "Fn", "ctrl_r": "Right Ctrl", "ctrl_l": "Left Ctrl", "alt_r": "Right Alt", "shift_r": "Right Shift"}


def describe_keys(keys: list[KeySpec]) -> str:
    names = [_FRIENDLY.get(k.label, k.label.upper() if k.label.startswith("f") and k.label[1:].isdigit() else k.label) for k in keys]
    return " or ".join(dict.fromkeys(names))  # "ctrl" expands to two keys with one name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mitraa.assistant", description="Hold a key to summon Mitraa.")
    parser.add_argument("--detect", action="store_true", help="show raw key codes (use this to check your Fn key)")
    parser.add_argument("--install-startup", action="store_true", help="start the assistant when Windows starts")
    parser.add_argument("--uninstall-startup", action="store_true", help="stop starting with Windows")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure") and sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.detect:
        detect_keys()
        return 0
    if args.install_startup:
        print(f"Installed: {install_startup()}")
        return 0
    if args.uninstall_startup:
        print("Removed." if uninstall_startup() else "It was not installed.")
        return 0
    return run()


def run() -> int:
    _setup_logging(get_settings().mitraa_log_level)
    lock = _single_instance_lock()
    if lock is None:
        _say("Mitraa assistant is already running (look for its tray icon).")
        return 1

    settings = get_settings()
    try:
        keys = parse_hotkeys(settings.mitraa_hotkey)
    except ValueError as exc:
        _say(f"Bad MITRAA_HOTKEY in .env: {exc}")
        return 1
    hint = describe_keys(keys)

    _enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    # pythonw has no stderr: send UI errors to the log instead of losing them
    root.report_callback_exception = lambda *exc: logger.error("UI error", exc_info=exc)

    from mitraa.assistant.popup import AssistantWindow
    from mitraa.assistant.tray import start_tray
    from mitraa.assistant.voice import build_voice

    voice = build_voice(settings)  # Whisper loads in the background
    window = AssistantWindow(root, hint, voice)
    detector = HoldDetector(settings.mitraa_hold_seconds, lambda key: window.events.put(("summon", key)))
    listener = HotkeyListener(keys, detector)
    tray = None

    def quit_app() -> None:
        listener.stop()
        if tray is not None:
            tray.stop()
        window.worker.shutdown()
        if window.voice_input:
            window.voice_input.cancel()
        if voice.speaker:
            voice.speaker.close()
        root.quit()

    def toggle_pause() -> None:
        detector.enabled = not detector.enabled

    window.on_quit = quit_app
    try:
        listener.start()
    except RuntimeError as exc:
        _say(str(exc))
        return 1
    try:
        tray = start_tray(
            f"Mitraa: hold {hint} for {settings.mitraa_hold_seconds:g}s",
            on_open=lambda: window.events.put(("summon", "tray")),
            on_toggle_pause=toggle_pause,
            is_paused=lambda: not detector.enabled,
            on_quit=lambda: window.events.put(("quit", None)),
            greeting=f"Hold {hint} for {settings.mitraa_hold_seconds:g} seconds, then just talk.",
        )
    except Exception:
        logger.exception("Tray icon unavailable; continuing without it")

    _say(f"Mitraa assistant running. Hold {hint} for {settings.mitraa_hold_seconds:g} seconds. Ctrl+C or tray → Quit to exit.")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        quit_app()
    finally:
        lock.close()
    return 0


# ---------------------------------------------------------------- helpers

def _say(message: str) -> None:
    logger.info(message)
    if sys.stdout is not None:  # pythonw has no console
        print(message)


def _setup_logging(level: str = "INFO") -> None:
    log_dir = BACKEND_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(log_dir / "assistant.log", encoding="utf-8")]
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


def _single_instance_lock() -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        sock.close()
        return None
    return sock


def _enable_dpi_awareness() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if sys.platform != "win32" or not appdata:
        raise SystemExit("Start-with-Windows is only available on Windows.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def install_startup() -> Path:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    script = _startup_dir() / STARTUP_FILE
    script.write_text(
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.CurrentDirectory = "{BACKEND_DIR}"\r\n'
        f'shell.Run """{pythonw}"" -m mitraa.assistant", 0, False\r\n',
        encoding="utf-8",
    )
    return script


def uninstall_startup() -> bool:
    script = _startup_dir() / STARTUP_FILE
    if not script.exists():
        return False
    script.unlink()  # our own launcher file, not a user file
    return True
