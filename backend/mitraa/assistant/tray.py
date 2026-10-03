"""System-tray icon with Open / Pause hotkey / Quit."""

from __future__ import annotations

from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw


def _icon_image(size: int = 64) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, size - 2, size - 2), fill=(124, 156, 255, 255))
    pad = size // 4
    # a simple "M" mark
    draw.line(
        [(pad, size - pad), (pad, pad), (size // 2, size // 2 + 4), (size - pad, pad), (size - pad, size - pad)],
        fill=(23, 24, 28, 255),
        width=max(3, size // 12),
        joint="curve",
    )
    return image


def start_tray(
    tooltip: str,
    on_open: Callable[[], None],
    on_toggle_pause: Callable[[], None],
    is_paused: Callable[[], bool],
    on_quit: Callable[[], None],
    greeting: str = "",
) -> pystray.Icon:
    menu = pystray.Menu(
        pystray.MenuItem("Open Mitraa", lambda _i, _m: on_open(), default=True),
        pystray.MenuItem("Pause hotkey", lambda _i, _m: on_toggle_pause(), checked=lambda _m: is_paused()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", lambda _i, _m: on_quit()),
    )
    icon = pystray.Icon("mitraa", _icon_image(), tooltip, menu)

    def setup(tray: pystray.Icon) -> None:
        tray.visible = True
        if greeting:
            tray.notify(greeting, "Mitraa is running")

    icon.run_detached(setup)
    return icon
