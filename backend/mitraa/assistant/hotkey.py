"""Global hold-to-summon hotkey.

Only the configured trigger keys are inspected; nothing else is recorded or logged.
On Windows the low-level hook gives raw virtual-key and scan codes, which is the only
way to see keys like Fn on keyboards that report it at all.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Windows virtual-key codes. Low-level hooks report left/right modifiers separately.
NAMED_VKS: dict[str, int] = {
    "ctrl_r": 0xA3, "ctrl_l": 0xA2, "alt_r": 0xA5, "alt_l": 0xA4,
    "shift_r": 0xA1, "shift_l": 0xA0, "win_l": 0x5B, "win_r": 0x5C,
    "caps_lock": 0x14, "scroll_lock": 0x91, "pause": 0x13, "menu": 0x5D,
    "insert": 0x2D, "home": 0x24, "end": 0x23,
    # Keyboards that expose Fn to Windows usually send VK 0xFF (no standard mapping).
    "fn": 0xFF,
    **{f"f{n}": 0x6F + n for n in range(1, 25)},
}
# Names that match more than one physical key.
KEY_GROUPS: dict[str, tuple[str, ...]] = {
    "ctrl": ("ctrl_l", "ctrl_r"),
    "alt": ("alt_l", "alt_r"),
    "shift": ("shift_l", "shift_r"),
}

_WM_KEYDOWN, _WM_KEYUP, _WM_SYSKEYDOWN, _WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
_LLKHF_INJECTED = 0x10


@dataclass(frozen=True)
class KeySpec:
    label: str
    vk: int | None = None
    scan: int | None = None

    def matches(self, vk: int, scan: int) -> bool:
        if self.vk is not None and self.vk != vk:
            return False
        if self.scan is not None and self.scan != scan:
            return False
        return True


def parse_hotkeys(spec: str) -> list[KeySpec]:
    """Parse e.g. "ctrl", "fn,ctrl_r", "vk:255", "scan:99" or "vk:255/scan:99"."""
    keys: list[KeySpec] = []
    for raw in spec.split(","):
        item = raw.strip().lower()
        if not item:
            continue
        if item in KEY_GROUPS:  # either side counts as the same trigger
            keys.extend(KeySpec(item, vk=NAMED_VKS[name]) for name in KEY_GROUPS[item])
            continue
        if item in NAMED_VKS:
            keys.append(KeySpec(item, vk=NAMED_VKS[item]))
            continue
        vk = scan = None
        for part in item.split("/"):
            kind, _, value = part.partition(":")
            if kind not in ("vk", "scan") or not value:
                raise ValueError(f"Unknown hotkey '{raw.strip()}'. Use a name like ctrl_r, or vk:N / scan:N.")
            number = int(value, 0)
            if kind == "vk":
                vk = number
            else:
                scan = number
        keys.append(KeySpec(item, vk=vk, scan=scan))
    if not keys:
        raise ValueError("No hotkey configured.")
    return keys


class HoldDetector:
    """Fires on_trigger once when a trigger key has been held alone for hold_seconds.

    - Auto-repeat key-downs while holding are ignored (timing starts at the first press).
    - Releasing early cancels.
    - Pressing any other key during the hold (e.g. Ctrl+C) spoils it until release,
      so normal shortcuts never summon the assistant.
    - Lost key-ups are handled with is_down(key): the OS is asked whether the key is
      physically down. When the OS can't tell (e.g. Fn reported as VK 0xFF), the hold
      only stays alive while auto-repeat key-downs keep arriving (stale_after).
    """

    def __init__(
        self,
        hold_seconds: float,
        on_trigger: Callable[[str], None],
        clock: Callable[[], float] = time.monotonic,
        stale_after: float = 1.5,
        is_down: Callable[[str], bool | None] | None = None,
    ) -> None:
        self.hold_seconds = hold_seconds
        self.stale_after = stale_after
        self.on_trigger = on_trigger
        self.clock = clock
        self.is_down = is_down
        self.enabled = True
        self._lock = threading.Lock()
        self._held: str | None = None
        self._since = 0.0
        self._last_seen = 0.0
        self._fired = False
        self._spoiled = False

    def press(self, key_id: str, is_trigger: bool) -> None:
        with self._lock:
            if not is_trigger:
                if self._held is not None and not self._spoiled:
                    self._spoiled = True
                    logger.debug("Hold of %s spoiled by another key", self._held)
                return
            now = self.clock()
            if self._held is None:
                self._held, self._since, self._fired, self._spoiled = key_id, now, False, False
                self._last_seen = now
                logger.debug("Hold of %s started", key_id)
            elif self._held == key_id:
                self._last_seen = now  # auto-repeat
            else:
                self._spoiled = True

    def release(self, key_id: str) -> None:
        with self._lock:
            if key_id == self._held:
                logger.debug("Hold of %s released after %.2fs", key_id, self.clock() - self._since)
                self._held = None

    def tick(self) -> None:
        with self._lock:
            if self._held is not None and not self._still_held(self._held):
                logger.info("Dropped hold of %s: key is no longer down", self._held)
                self._held = None
            ready = (
                self.enabled
                and self._held is not None
                and not self._fired
                and not self._spoiled
                and self.clock() - self._since >= self.hold_seconds
            )
            if ready:
                self._fired = True
            key = self._held
        if ready and key is not None:
            logger.info("Hotkey %s held for %.1fs: summoning", key, self.hold_seconds)
            self.on_trigger(key)

    def _still_held(self, key: str) -> bool:
        down = self.is_down(key) if self.is_down else None
        if down is not None:
            return down
        return self.clock() - self._last_seen <= self.stale_after


class HotkeyListener:
    """Feeds raw key events into a HoldDetector via a pynput low-level hook."""

    def __init__(self, keys: list[KeySpec], detector: HoldDetector, tick_interval: float = 0.05) -> None:
        self.keys = keys
        self.detector = detector
        self.tick_interval = tick_interval
        self._listener = None
        self._stop = threading.Event()
        if detector.is_down is None and sys.platform == "win32":
            detector.is_down = self.is_physically_down

    def classify(self, vk: int, scan: int) -> str | None:
        for key in self.keys:
            if key.matches(vk, scan):
                return key.label
        return None

    def is_physically_down(self, label: str) -> bool | None:
        """Ask Windows whether any key with this label is down; None if it can't know (e.g. Fn)."""
        vks = [k.vk for k in self.keys if k.label == label and k.vk is not None and k.vk != 0xFF]
        if not vks:
            return None
        try:
            import ctypes

            get_state = ctypes.windll.user32.GetAsyncKeyState
            return any(get_state(vk) & 0x8000 for vk in vks)
        except (AttributeError, OSError):
            return None

    def handle_raw(self, msg: int, vk: int, scan: int, flags: int) -> None:
        if flags & _LLKHF_INJECTED:
            return  # synthetic input (including our own focus trick)
        label = self.classify(vk, scan)
        if msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
            self.detector.press(label or "other", is_trigger=label is not None)
        elif msg in (_WM_KEYUP, _WM_SYSKEYUP) and label is not None:
            self.detector.release(label)

    def start(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("The hold-to-summon hotkey is currently implemented for Windows only.")
        from pynput import keyboard

        def event_filter(msg, data):  # runs inside the hook: keep it fast
            try:
                self.handle_raw(msg, data.vkCode, data.scanCode, data.flags)
            except Exception:
                logger.exception("Hotkey handler failed")
            return False  # we don't need pynput's own key objects; the event still reaches apps

        self._listener = keyboard.Listener(win32_event_filter=event_filter)
        self._listener.start()
        threading.Thread(target=self._tick_loop, name="mitraa-hotkey-tick", daemon=True).start()
        logger.info("Hotkey listener started for: %s", ", ".join(dict.fromkeys(k.label for k in self.keys)))

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.stop()

    def _tick_loop(self) -> None:
        while not self._stop.wait(self.tick_interval):
            try:
                self.detector.tick()
            except Exception:
                logger.exception("Hotkey tick failed")


def detect_keys() -> None:
    """Print the raw codes of every key pressed so the user can find their Fn key. Esc quits."""
    if sys.platform != "win32":
        raise SystemExit("Key detection is Windows-only for now.")
    from pynput import keyboard

    names = {vk: name for name, vk in NAMED_VKS.items()}
    print("Press the key you want to use (e.g. Fn). Press Esc to quit.")
    print("If nothing appears when you press Fn, your keyboard handles Fn in hardware and")
    print("Windows can't see it; use another key such as ctrl_r instead.\n")
    done = threading.Event()

    def event_filter(msg, data):
        if msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN, _WM_KEYUP, _WM_SYSKEYUP):
            kind = "down" if msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN) else "up  "
            name = names.get(data.vkCode, "")
            print(f"  {kind}  vk={data.vkCode:<4} (0x{data.vkCode:02X})  scan={data.scanCode:<4} {name}")
            if data.vkCode == 0x1B and kind == "up  ":
                done.set()
        return False

    with keyboard.Listener(win32_event_filter=event_filter):
        done.wait()
    print("\nSet the key in backend/.env, e.g. MITRAA_HOTKEY=vk:255 or MITRAA_HOTKEY=scan:99")
