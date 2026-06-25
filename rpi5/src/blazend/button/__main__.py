"""HAT push-to-talk button → activation marker for ``blazend-audio-in``.

Jessica is deaf by default and only listens when activated (by her wake word or
this button). This watcher monitors the ReSpeaker 2-Mics Pi HAT button — GPIO17,
active-low with an internal pull-up — via ``gpiomon`` (libgpiod v2) and touches
``<runtime>/activate`` on every press. ``blazend-audio-in`` consumes that marker
to open one listen window. Shelling out to ``gpiomon`` keeps this dependency-free
(no Python GPIO library) and matches the rest of the appliance's gpiod usage.
"""

from __future__ import annotations

import os
import subprocess
import time

_RUNTIME = os.environ.get("BLAZEN_RUNTIME_DIR", "/run/blazen")
_MARKER = os.path.join(_RUNTIME, "activate")
_CHIP = os.environ.get("BLAZEN_BUTTON_CHIP", "gpiochip0")
_LINE = os.environ.get("BLAZEN_BUTTON_LINE", "17")


def _wait_for_press() -> bool:
    """Block until one falling edge (press). True on a real edge, False on error.

    Active-low button + pull-up: released = high, pressed pulls low → the press is
    a falling edge. ``-n 1`` returns after the first event.
    """
    try:
        proc = subprocess.run(
            ["gpiomon", "-c", _CHIP, "-b", "pull-up", "-e", "falling", "-n", "1", _LINE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        return False
    return proc.returncode == 0


def _activate() -> None:
    try:
        with open(_MARKER, "w"):
            pass
        print("button press -> activate", flush=True)
    except OSError as exc:  # /run not writable yet, etc. — keep watching
        print(f"could not write {_MARKER}: {exc}", flush=True)


def main() -> None:
    print(f"blazend-button: watching {_CHIP} line {_LINE} -> {_MARKER}", flush=True)
    while True:
        if _wait_for_press():
            _activate()
            time.sleep(0.3)  # debounce a single press
        else:
            time.sleep(1.0)  # gpiomon missing / no HAT — back off and retry


if __name__ == "__main__":
    main()
