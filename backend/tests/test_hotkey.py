from __future__ import annotations

import pytest

from mitraa.assistant.app import describe_keys
from mitraa.assistant.hotkey import HoldDetector, HotkeyListener, KeySpec, parse_hotkeys

DOWN, UP = 0x100, 0x101


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def setup():
    clock = Clock()
    fired: list[str] = []
    # is_down -> None: 'OS can't tell', so these tests exercise the auto-repeat fallback
    detector = HoldDetector(3.0, fired.append, clock=clock, stale_after=1.5, is_down=lambda key: None)
    return clock, fired, detector


def hold(detector: HoldDetector, clock: Clock, key: str, seconds: float, step: float = 0.05) -> None:
    """Simulate holding a key: the OS sends auto-repeat key-downs while it's down."""
    detector.press(key, is_trigger=True)
    elapsed = 0.0
    while elapsed < seconds:
        clock.now += step
        elapsed += step
        detector.press(key, is_trigger=True)  # auto-repeat
        detector.tick()


def test_fires_after_hold(setup):
    clock, fired, detector = setup
    hold(detector, clock, "fn", 3.1)
    assert fired == ["fn"]


def test_fires_only_once_per_hold(setup):
    clock, fired, detector = setup
    hold(detector, clock, "fn", 8)
    assert fired == ["fn"]


def test_early_release_cancels(setup):
    clock, fired, detector = setup
    hold(detector, clock, "fn", 2.5)
    detector.release("fn")
    clock.now += 5
    detector.tick()
    assert fired == []


def test_can_fire_again_after_release(setup):
    clock, fired, detector = setup
    hold(detector, clock, "fn", 3.1)
    detector.release("fn")
    hold(detector, clock, "fn", 3.1)
    assert fired == ["fn", "fn"]


def test_timing_starts_at_first_press_not_repeats(setup):
    clock, fired, detector = setup
    hold(detector, clock, "ctrl_r", 2.0)
    assert fired == []
    hold(detector, clock, "ctrl_r", 1.1)  # still held; total > 3s
    assert fired == ["ctrl_r"]


def test_shortcut_chord_never_fires(setup):
    clock, fired, detector = setup
    detector.press("ctrl_r", is_trigger=True)
    detector.press("other:67", is_trigger=False)  # Ctrl+C
    hold(detector, clock, "ctrl_r", 5)
    assert fired == []


def test_lost_key_up_does_not_fire(setup):
    """Laptop Fn combos can send a key-down with no key-up and no repeats."""
    clock, fired, detector = setup
    detector.press("fn", is_trigger=True)
    for _ in range(100):
        clock.now += 0.05
        detector.tick()
    assert fired == []


def test_paused_detector_does_not_fire(setup):
    clock, fired, detector = setup
    detector.enabled = False
    hold(detector, clock, "fn", 4)
    assert fired == []


def test_parse_hotkeys():
    keys = parse_hotkeys("fn, ctrl_r, vk:0x7B, scan:99, vk:255/scan:99")
    assert keys[0] == KeySpec("fn", vk=0xFF)
    assert keys[1] == KeySpec("ctrl_r", vk=0xA3)
    assert keys[2].vk == 0x7B and keys[2].scan is None
    assert keys[3].scan == 99 and keys[3].vk is None
    assert keys[4].matches(255, 99) and not keys[4].matches(255, 98)
    with pytest.raises(ValueError):
        parse_hotkeys("banana")
    with pytest.raises(ValueError):
        parse_hotkeys(" , ")


def test_describe_keys():
    assert describe_keys(parse_hotkeys("fn,ctrl_r")) == "Fn or Right Ctrl"
    assert describe_keys(parse_hotkeys("f12")) == "F12"


def test_listener_routes_raw_events(setup):
    clock, fired, detector = setup
    listener = HotkeyListener(parse_hotkeys("fn,ctrl_r"), detector)
    listener.handle_raw(DOWN, 0xA3, 0x1D, 0)
    for _ in range(62):
        clock.now += 0.05
        listener.handle_raw(DOWN, 0xA3, 0x1D, 0)
        detector.tick()
    assert fired == ["ctrl_r"]


def test_listener_ignores_injected_events(setup):
    clock, fired, detector = setup
    listener = HotkeyListener(parse_hotkeys("ctrl_r"), detector)
    for _ in range(80):
        listener.handle_raw(DOWN, 0xA3, 0x1D, 0x10)  # LLKHF_INJECTED
        clock.now += 0.05
        detector.tick()
    assert fired == []


def test_listener_other_key_spoils_hold(setup):
    clock, fired, detector = setup
    listener = HotkeyListener(parse_hotkeys("ctrl_r"), detector)
    listener.handle_raw(DOWN, 0xA3, 0x1D, 0)
    listener.handle_raw(DOWN, 0x43, 0x2E, 0)  # C
    listener.handle_raw(UP, 0x43, 0x2E, 0)
    for _ in range(80):
        clock.now += 0.05
        listener.handle_raw(DOWN, 0xA3, 0x1D, 0)
        detector.tick()
    assert fired == []


def test_ctrl_means_either_side(setup):
    clock, fired, detector = setup
    keys = parse_hotkeys("ctrl")
    assert {k.vk for k in keys} == {0xA2, 0xA3} and describe_keys(keys) == "Ctrl"
    listener = HotkeyListener(keys, detector)
    for vk in (0xA2, 0xA3):
        listener.handle_raw(DOWN, vk, 0x1D, 0)
        for _ in range(62):
            clock.now += 0.05
            listener.handle_raw(DOWN, vk, 0x1D, 0)
            detector.tick()
        listener.handle_raw(UP, vk, 0x1D, 0)
    assert fired == ["ctrl", "ctrl"]


def test_ctrl_ignores_letters_and_fn(setup):
    clock, fired, detector = setup
    listener = HotkeyListener(parse_hotkeys("ctrl"), detector)
    for vk in (0x4D, 0xFF, 0x5D):  # M, Fn-style 0xFF, Menu
        for _ in range(80):
            listener.handle_raw(DOWN, vk, 0x32, 0)
            clock.now += 0.05
            detector.tick()
        listener.handle_raw(UP, vk, 0x32, 0)
    assert fired == []


def test_physical_key_state_works_without_auto_repeat():
    """The bug this guards: Ctrl held for 3s with no auto-repeat must still fire."""
    clock, fired, physically_down = Clock(), [], {"ctrl": True}
    detector = HoldDetector(3.0, fired.append, clock=clock, is_down=lambda key: physically_down[key])
    detector.press("ctrl", is_trigger=True)
    for _ in range(62):  # no repeats at all
        clock.now += 0.05
        detector.tick()
    assert fired == ["ctrl"]


def test_lost_key_up_is_caught_by_physical_state():
    clock, fired, physically_down = Clock(), [], {"ctrl": True}
    detector = HoldDetector(3.0, fired.append, clock=clock, is_down=lambda key: physically_down[key])
    detector.press("ctrl", is_trigger=True)
    clock.now += 1.0
    detector.tick()
    physically_down["ctrl"] = False  # key-up event was never delivered
    for _ in range(80):
        clock.now += 0.05
        detector.tick()
    assert fired == []


def test_fn_has_no_physical_state():
    detector = HoldDetector(3.0, lambda key: None, is_down=lambda key: None)
    listener = HotkeyListener(parse_hotkeys("fn,ctrl"), detector)
    assert listener.is_physically_down("fn") is None
